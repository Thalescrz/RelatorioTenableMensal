from __future__ import annotations

import json
import inspect
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

from tenable_reports.application.orchestration import SAFE_ID_PATTERN
from tenable_reports.application.orchestration import load_orchestration_config
from tenable_reports.application.postgresql_migration import (
    MainBackfillSourceState,
    main_backfill_source_state,
)
from tenable_reports.application.publishing import write_json_atomic
from tenable_reports.application.report_main_backfill import (
    MainBackfillPlan,
    plan_main_backfill,
)
from tenable_reports.application.report_registry import (
    MainDeletionRequiresDecision,
    ReportRegistry,
)
from tenable_reports.application.storage_guard import required_free_bytes
from tenable_reports.application.tag_scope import parse_tag_values
from tenable_reports.application.retention import (
    TRANSIENT_CATEGORIES,
    RetentionCandidate,
    RetentionPolicy,
    apply_cleanup_plan,
    plan_orchestration_log_cleanup,
    apply_retention,
    plan_published_run_cleanup,
    plan_tiered_retention,
)
from tenable_reports.config.database import DatabaseConfig
from tenable_reports.config.environment import CredentialConfig, load_dotenv_file
from tenable_reports.infrastructure.postgresql import (
    PostgresDatabase,
    PostgresOperationsRepository,
    SCHEMA_NAME,
)
from tenable_reports.infrastructure.report_registry_postgresql import PostgresReportRegistry
from tenable_reports.domain.report_reference import reference_key_for_candidate
from tenable_reports.infrastructure.tenable_vm.client import TenableVmClient, TenableVmConfig


STATIC_DIRECTORY = Path(__file__).with_name("static")
MAX_REQUEST_BYTES = 64 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SECRET_PATTERN = re.compile(
    r"(?i)(TENABLE_(?:ACCESS|SECRET)|TCS_API_SECRET|TENABLE_REPORTS_DB_PASSWORD)\s*=\s*[^\s]+"
)
BASE_INTELLIGENCE_MODULES = (
    "vm_monthly_volume",
    "vm_previous_period_delta",
    "vm_network_comparison",
    "scan_auth_health",
    "vm_plugin_family",
    "vm_eol_software",
    "vm_executive_evolution",
    "vm_monthly_evolution",
    "vm_exploit_vector",
)
BACKFILL_CONFIRMATION = "APLICAR BACKFILL"


def _intelligence_modules(*, enabled: bool, was_enabled: bool, cloud_enabled: bool) -> list[str]:
    if not enabled:
        return []
    modules = list(BASE_INTELLIGENCE_MODULES)
    if cloud_enabled:
        modules.append("cloud_container_images")
    if was_enabled:
        modules.append("was_unsupported_tech")
    return modules


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(value: str, *, limit: int = 3000) -> str:
    sanitized = SECRET_PATTERN.sub(r"\1=[REMOVIDO]", value.strip())
    if "Traceback (" in sanitized or re.search(r'\bFile "[A-Za-z]:\\', sanitized):
        exception_names = list(re.finditer(
            r"\b[A-Z][A-Za-z0-9_.]*(?:Error|Exception)\b", sanitized
        ))
        if exception_names:
            sanitized = sanitized[exception_names[-1].start():]
    lines = []
    for line in sanitized.splitlines():
        stripped = line.strip()
        if stripped.startswith("Traceback ("):
            continue
        if re.match(r'^File ".+", line \d+', stripped):
            continue
        if line.startswith(("    ", "\t")):
            continue
        if stripped:
            lines.append(stripped)
    sanitized = "\n".join(lines)
    return sanitized[-limit:] or "Falha sem mensagem detalhada."


def _relative_path(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path, base)).as_posix()


def slugify_client_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", ascii_value).strip("-._")
    slug = re.sub(r"[-_.]{2,}", "-", slug)[:80].rstrip("-._")
    return slug or "cliente"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"O arquivo {path.name} precisa conter um objeto JSON.")
    return payload


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _credential_status(path: Path) -> tuple[bool, bool]:
    if not path.is_file():
        return False, False
    try:
        values = _read_env_values(path)
    except OSError:
        return True, False
    return True, bool(values.get("TENABLE_ACCESS") and values.get("TENABLE_SECRET"))


def check_tenable_connection(path: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        credentials = CredentialConfig.from_environment(_read_env_values(path))
        if not credentials.is_complete:
            raise ValueError("Credenciais Tenable incompletas.")
        client = TenableVmClient(TenableVmConfig(
            access_key=credentials.access_key,
            secret_key=credentials.secret_key,
            base_url=credentials.base_url,
            timeout_seconds=credentials.timeout_seconds,
            ca_bundle=credentials.ca_bundle,
            validate_tls=credentials.validate_tls,
        ))
        client.list_export_jobs()
    except Exception as exc:  # O resultado do teste deve voltar para a interface.
        return {
            "ok": False,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "message": _safe_error(str(exc), limit=300),
            "checked_at": _utc_now(),
        }
    return {
        "ok": True,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "message": "Conexao com a API Tenable funcionando.",
        "checked_at": _utc_now(),
    }


def list_tenable_tags(path: Path) -> list[dict[str, str]]:
    credentials = CredentialConfig.from_environment(_read_env_values(path))
    if not credentials.is_complete:
        raise ValueError("Credenciais Tenable incompletas.")
    client = TenableVmClient(TenableVmConfig(
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
        base_url=credentials.base_url,
        timeout_seconds=credentials.timeout_seconds,
        ca_bundle=credentials.ca_bundle,
        validate_tls=credentials.validate_tls,
    ))
    return [{
        "tag_uuid": item.uuid,
        "category_uuid": item.category_uuid,
        "category_name": item.category_name,
        "value": item.value,
    } for item in parse_tag_values(client.list_tag_values())]


class DashboardConfigStore:
    """Gerencia a carteira sem expor o conteudo dos arquivos de credenciais."""

    def __init__(self, *, project_root: Path, config_path: Path) -> None:
        self.project_root = project_root.resolve()
        self.config_path = config_path.resolve()
        self._lock = threading.RLock()
        self.ensure_exists()

    def ensure_exists(self) -> None:
        if self.config_path.exists():
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "orchestration_id": "carteira-tenable",
            "defaults": {
                "output_root": _relative_path(self.project_root / "data", self.config_path.parent),
                "template": _relative_path(
                    self.project_root / "templates" / "corporate" / "base-v1.docx",
                    self.config_path.parent,
                ),
                "assets_dir": _relative_path(
                    self.project_root / "templates" / "corporate" / "assets",
                    self.config_path.parent,
                ),
                "database_env_file": _relative_path(
                    self.project_root / "credentials" / "database.env",
                    self.config_path.parent,
                ),
                "max_parallel": 1,
                "retention_days": 395,
                "failed_staging_days": 7,
                "logs_days": 90,
                "cleanup_after_publish": True,
                "include_output": False,
                "include_software_vulns": False,
                "mask_sensitive": False,
            },
            "clients": [],
        }
        write_json_atomic(self.config_path, payload)

    def raw(self) -> dict[str, Any]:
        with self._lock:
            return _read_json(self.config_path)

    def database_env_path(self) -> Path:
        payload = self.raw()
        raw = str((payload.get("defaults") or {}).get("database_env_file") or "../credentials/database.env")
        return (self.config_path.parent / raw).resolve()

    def client_env_path(self, client_id: str) -> Path:
        payload = self.raw()
        client = next(
            (
                item for item in payload.get("clients") or []
                if isinstance(item, Mapping) and item.get("client_id") == client_id
            ),
            None,
        )
        if client is None:
            raise KeyError("Cliente nao encontrado.")
        return (self.config_path.parent / str(client.get("env_file") or "")).resolve()

    def list_clients(self) -> list[dict[str, Any]]:
        payload = self.raw()
        result: list[dict[str, Any]] = []
        for raw in payload.get("clients") or []:
            if not isinstance(raw, Mapping):
                continue
            client_id = str(raw.get("client_id") or "")
            profile_path = (self.config_path.parent / str(raw.get("profile") or "")).resolve()
            env_path = (self.config_path.parent / str(raw.get("env_file") or "")).resolve()
            profile: dict[str, Any] = {}
            profile_error: str | None = None
            try:
                profile = _read_json(profile_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                profile_error = _safe_error(str(exc), limit=300)
            env_exists, credentials_ready = _credential_status(env_path)
            report = profile.get("report") if isinstance(profile.get("report"), Mapping) else {}
            tag_reports = (
                report.get("tag_reports")
                if isinstance(report.get("tag_reports"), Mapping) else {}
            )
            presentation = (
                profile.get("presentation")
                if isinstance(profile.get("presentation"), Mapping) else {}
            )
            result.append({
                "client_id": client_id,
                "display_name": str(
                    raw.get("display_name") or profile.get("display_name") or client_id
                ),
                "tenant_id": str(profile.get("tenant_id") or ""),
                "enabled": bool(raw.get("enabled", True)),
                "tags": list(raw.get("tags") or []),
                "profile_exists": profile_path.is_file(),
                "profile_error": profile_error,
                "env_exists": env_exists,
                "credentials_ready": credentials_ready,
                "was_enabled": bool(((profile.get("scope") or {}).get("was") or {}).get("enabled")),
                "cloud_enabled": bool(
                    ((profile.get("scope") or {}).get("cloud_security") or {}).get("enabled")
                ),
                "intelligence_enabled": bool(report.get("intelligence_modules") or []),
                "include_output": bool(raw.get("include_output", False)),
                "show_source_filters": bool(presentation.get("show_source_filters", False)),
                "tag_reports_enabled": bool(tag_reports.get("enabled", False)),
                "tag_reports": self._tag_reports(tag_reports.get("tags")),
            })
        return result

    def add_client(self, values: Mapping[str, Any]) -> dict[str, Any]:
        display_name = str(values.get("display_name") or "").strip()
        client_id = str(values.get("client_id") or "").strip() or slugify_client_id(display_name)
        tenant_id = str(values.get("tenant_id") or "").strip() or client_id
        if not SAFE_ID_PATTERN.fullmatch(client_id):
            raise ValueError("O ID deve usar apenas letras, numeros, ponto, _ ou -.")
        if not display_name:
            raise ValueError("Informe o nome do cliente.")
        if not tenant_id:
            raise ValueError("Informe o tenant do cliente.")
        tags = self._tags(values.get("tags"))
        tag_reports = self._tag_reports(values.get("tag_reports"))
        tag_reports_enabled = bool(values.get("tag_reports_enabled", False))
        access_key = self._secret(values.get("access_key"), "Access Key")
        secret_key = self._secret(values.get("secret_key"), "Secret Key")
        if bool(access_key) != bool(secret_key):
            raise ValueError("Informe as duas chaves ou deixe ambas em branco.")
        with self._lock:
            payload = _read_json(self.config_path)
            clients = payload.setdefault("clients", [])
            if any(item.get("client_id") == client_id for item in clients if isinstance(item, Mapping)):
                raise ValueError("Ja existe um cliente com esse ID.")
            profile_path = self.project_root / "clients" / "managed" / f"{client_id}.json"
            env_path = self.project_root / "credentials" / f"{client_id}.env"
            if profile_path.exists() or env_path.exists():
                raise ValueError("Ja existem arquivos locais reservados para esse ID.")
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.parent.mkdir(parents=True, exist_ok=True)
            was_enabled = bool(values.get("was_enabled", False))
            cloud_enabled = bool(values.get("cloud_enabled", False))
            intelligence_enabled = bool(values.get("intelligence_enabled", True))
            intelligence_modules = _intelligence_modules(
                enabled=intelligence_enabled,
                was_enabled=was_enabled,
                cloud_enabled=cloud_enabled,
            )
            profile = {
                "schema_version": 1,
                "client_id": client_id,
                "display_name": display_name,
                "tenant_id": tenant_id,
                "report": {
                    "type": "vulnerabilities",
                    "base_modules": ["summary", "infrastructure", "vm_top5", "was", "was_top5"],
                    "intelligence_modules": intelligence_modules,
                    "network_comparison_tags": tags,
                    "tag_reports": {
                        "enabled": tag_reports_enabled,
                        "tags": tag_reports,
                    },
                },
                "scope": {
                    "vm": {"asset_groups": [], "include_unlicensed": False},
                    "was": {"enabled": was_enabled, "application_ids": []},
                    "cloud_security": {"enabled": cloud_enabled},
                },
                "presentation": {
                    "locale": "pt-BR",
                    "vm_top5_include_output": bool(values.get("include_output", False)),
                    "was_top5_include_output": bool(values.get("include_output", False)),
                    "show_source_filters": bool(values.get("show_source_filters", False)),
                },
                "reporting": {
                    "timezone": "America/Fortaleza",
                    "default_period": "previous_calendar_month",
                    "manual_default_period": "rolling_calendar_month",
                    "include_info_severity": False,
                    "top_assets_limit": 10,
                    "top_vulnerabilities_limit": 5,
                    "late_collection_grace_days": 1,
                },
            }
            write_json_atomic(profile_path, profile)
            env_path.write_text(
                "\n".join((
                    "TENABLE_BASE_URL=https://cloud.tenable.com",
                    f"TENABLE_ACCESS={access_key}",
                    f"TENABLE_SECRET={secret_key}",
                    "TENABLE_HTTP_TIMEOUT_SECONDS=30",
                    "TENABLE_VALIDATE_TLS=true",
                    "",
                )),
                encoding="utf-8",
            )
            clients.append({
                "client_id": client_id,
                "display_name": display_name,
                "profile": _relative_path(profile_path, self.config_path.parent),
                "env_file": _relative_path(env_path, self.config_path.parent),
                "enabled": True,
                "tags": tags,
                "include_output": bool(values.get("include_output", False)),
            })
            write_json_atomic(self.config_path, payload)
        return next(item for item in self.list_clients() if item["client_id"] == client_id)

    def update_client(self, client_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = _read_json(self.config_path)
            client = next(
                (item for item in payload.get("clients") or [] if item.get("client_id") == client_id),
                None,
            )
            if client is None:
                raise KeyError("Cliente nao encontrado.")
            if "enabled" in values:
                client["enabled"] = bool(values["enabled"])
            profile_path = (self.config_path.parent / str(client["profile"])).resolve()
            profile = _read_json(profile_path)
            profile_changed = False
            if "display_name" in values:
                display_name = str(values.get("display_name") or "").strip()
                if not display_name:
                    raise ValueError("Informe o nome do cliente.")
                client["display_name"] = display_name
                profile["display_name"] = display_name
                profile_changed = True
            if "tenant_id" in values:
                tenant_id = str(values.get("tenant_id") or "").strip()
                if not tenant_id:
                    raise ValueError("Informe o tenant do cliente.")
                profile["tenant_id"] = tenant_id
                profile_changed = True
            if "tags" in values:
                client["tags"] = self._tags(values.get("tags"))
                profile.setdefault("report", {})["network_comparison_tags"] = list(client["tags"])
                profile_changed = True
            if "include_output" in values:
                include_output = bool(values["include_output"])
                client["include_output"] = include_output
                presentation = profile.setdefault("presentation", {})
                presentation["vm_top5_include_output"] = include_output
                presentation["was_top5_include_output"] = include_output
                profile_changed = True
            if "show_source_filters" in values:
                profile.setdefault("presentation", {})["show_source_filters"] = bool(
                    values["show_source_filters"]
                )
                profile_changed = True
            if "tag_reports_enabled" in values or "tag_reports" in values:
                current = profile.setdefault("report", {}).get("tag_reports")
                current = current if isinstance(current, Mapping) else {}
                selected = (
                    self._tag_reports(values.get("tag_reports"))
                    if "tag_reports" in values
                    else self._tag_reports(current.get("tags"))
                )
                enabled = bool(
                    values.get("tag_reports_enabled", current.get("enabled", False))
                )
                profile.setdefault("report", {})["tag_reports"] = {
                    "enabled": enabled,
                    "tags": selected,
                }
                profile_changed = True
            capability_fields = {"was_enabled", "cloud_enabled", "intelligence_enabled"}
            if capability_fields.intersection(values):
                scope = profile.setdefault("scope", {})
                was_scope = scope.setdefault("was", {"application_ids": []})
                cloud_scope = scope.setdefault("cloud_security", {})
                current_modules = (profile.get("report") or {}).get("intelligence_modules") or []
                was_enabled = bool(values.get("was_enabled", was_scope.get("enabled", False)))
                cloud_enabled = bool(
                    values.get("cloud_enabled", cloud_scope.get("enabled", False))
                )
                intelligence_enabled = bool(
                    values.get("intelligence_enabled", bool(current_modules))
                )
                was_scope["enabled"] = was_enabled
                cloud_scope["enabled"] = cloud_enabled
                profile.setdefault("report", {})["intelligence_modules"] = (
                    _intelligence_modules(
                        enabled=intelligence_enabled,
                        was_enabled=was_enabled,
                        cloud_enabled=cloud_enabled,
                    )
                )
                profile_changed = True
            access_key = self._secret(values.get("access_key"), "Access Key")
            secret_key = self._secret(values.get("secret_key"), "Secret Key")
            if access_key or secret_key:
                if not (access_key and secret_key):
                    raise ValueError("Para trocar credenciais, informe as duas chaves.")
                env_path = (self.config_path.parent / str(client["env_file"])).resolve()
                env_path.write_text(
                    "\n".join((
                        "TENABLE_BASE_URL=https://cloud.tenable.com",
                        f"TENABLE_ACCESS={access_key}",
                        f"TENABLE_SECRET={secret_key}",
                        "TENABLE_HTTP_TIMEOUT_SECONDS=30",
                        "TENABLE_VALIDATE_TLS=true",
                        "",
                    )),
                    encoding="utf-8",
                )
            if profile_changed:
                write_json_atomic(profile_path, profile)
            write_json_atomic(self.config_path, payload)
        return next(item for item in self.list_clients() if item["client_id"] == client_id)

    @staticmethod
    def _secret(value: Any, label: str) -> str:
        result = str(value or "").strip()
        if "\n" in result or "\r" in result:
            raise ValueError(f"{label} contem caracteres invalidos.")
        return result

    @staticmethod
    def _tags(value: Any) -> list[str]:
        if value is None:
            return []
        items = value if isinstance(value, list) else str(value).split(",")
        result = [str(item).strip() for item in items if str(item).strip()]
        if len(result) != len(set(result)):
            result = list(dict.fromkeys(result))
        return result

    @staticmethod
    def _tag_reports(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tag_reports deve ser uma lista.")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, Mapping):
                raise ValueError("Cada TAG selecionada deve ser um objeto.")
            tag_uuid = str(raw.get("tag_uuid") or "").strip()
            category_uuid = str(raw.get("category_uuid") or "").strip()
            category_name = str(raw.get("category_name") or "").strip()
            tag_value = str(raw.get("value") or "").strip()
            if not tag_uuid or not category_name or not tag_value:
                raise ValueError("Cada TAG requer UUID, categoria e valor.")
            if tag_uuid in seen:
                raise ValueError(f"TAG duplicada: {tag_uuid}.")
            seen.add(tag_uuid)
            generate_report = bool(raw.get("generate_report", True))
            include_comparison = bool(
                raw.get("include_temporal_comparison", False)
            )
            if include_comparison and not generate_report:
                include_comparison = False
            result.append({
                "tag_uuid": tag_uuid,
                "category_uuid": category_uuid,
                "category_name": category_name,
                "value": tag_value,
                "generate_report": generate_report,
                "include_temporal_comparison": include_comparison,
            })
        return result


class DashboardDatabase:
    def __init__(self, env_path: Path) -> None:
        loaded = load_dotenv_file(env_path, override=True)
        self.database = PostgresDatabase(DatabaseConfig.from_environment(loaded))

    def summaries(self) -> dict[str, dict[str, Any]]:
        query = f"""
            select distinct on (r.client_id)
                r.client_id, r.run_id, r.status, r.period_id, r.ended_at,
                (select count(*) from {SCHEMA_NAME}.published_documents d
                 join {SCHEMA_NAME}.publications p2 on p2.publication_id = d.publication_id
                 where p2.run_id = r.run_id) as document_count
            from {SCHEMA_NAME}.report_runs r
            order by r.client_id, r.created_at desc
        """
        with self.database.connection() as connection:
            rows = connection.execute(query).fetchall()
        return {
            str(row[0]): {
                "run_id": row[1],
                "status": row[2],
                "period_id": row[3],
                "ended_at": row[4].isoformat() if row[4] else None,
                "document_count": int(row[5] or 0),
            }
            for row in rows
        }

    def reports(self, client_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        query = f"""
            select d.document_id, d.path, d.size_bytes, d.package_status,
                   r.run_id, r.period_id, r.execution_type, r.ended_at, p.created_at,
                   d.document_kind, d.tag_uuid, d.tag_category, d.tag_value
            from {SCHEMA_NAME}.published_documents d
            join {SCHEMA_NAME}.publications p on p.publication_id = d.publication_id
            join {SCHEMA_NAME}.report_runs r on r.run_id = p.run_id
            where r.client_id = %s
            order by p.created_at desc, d.document_id
            limit %s
        """
        with self.database.connection() as connection:
            rows = connection.execute(query, (client_id, limit)).fetchall()
        return [{
            "document_id": int(row[0]),
            "name": Path(str(row[1])).name,
            "path": str(row[1]),
            "size_bytes": int(row[2]),
            "package_status": row[3],
            "run_id": row[4],
            "period_id": row[5],
            "execution_type": row[6],
            "ended_at": row[7].isoformat() if row[7] else None,
            "created_at": row[8].isoformat() if row[8] else None,
            "document_kind": row[9],
            "tag_uuid": row[10],
            "tag_category": row[11],
            "tag_value": row[12],
        } for row in rows]

    def alerts(self, *, limit: int = 50) -> list[dict[str, Any]]:
        query = f"""
            select latest.client_id, latest.error, latest.ended_at,
                   latest.orchestration_run_id, latest.payload
            from (
                select distinct on (c.client_id)
                    c.client_id, c.status, c.error, c.ended_at,
                    c.orchestration_run_id, c.orchestration_client_id, c.payload
                from {SCHEMA_NAME}.orchestration_clients c
                order by c.client_id,
                         coalesce(c.ended_at, c.started_at) desc nulls last,
                         c.orchestration_client_id desc
            ) latest
            where latest.status = 'FAILED'
               or nullif(latest.error, '') is not null
               or (
                    jsonb_typeof(latest.payload -> 'warnings') = 'array'
                    and jsonb_array_length(latest.payload -> 'warnings') > 0
               )
            order by latest.ended_at desc nulls last,
                     latest.orchestration_client_id desc
            limit %s
        """
        with self.database.connection() as connection:
            rows = connection.execute(query, (limit,)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = row[4] if isinstance(row[4], Mapping) else {}
            warnings = payload.get("warnings") if isinstance(payload, Mapping) else []
            warning_messages = [
                str(item.get("message") or "")
                for item in warnings or []
                if isinstance(item, Mapping) and item.get("message")
            ]
            message = str(row[1] or " ".join(warning_messages) or "A geracao terminou com falha.")
            result.append({
                "client_id": row[0],
                "message": _safe_error(message, limit=500),
                "at": row[2].isoformat() if row[2] else None,
                "run_id": row[3],
            })
        return result

    def document(self, document_id: int) -> Path | None:
        query = f"select path from {SCHEMA_NAME}.published_documents where document_id = %s"
        with self.database.connection() as connection:
            row = connection.execute(query, (document_id,)).fetchone()
        return Path(str(row[0])).resolve() if row else None


ProgressCallback = Callable[[Mapping[str, Any]], None]
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(
    command: Sequence[str],
    cwd: Path,
    progress_callback: ProgressCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def drain_stderr() -> None:
        if process.stderr is not None:
            stderr_parts.extend(process.stderr)

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()
    if process.stdout is not None:
        for line in process.stdout:
            stdout_parts.append(line)
            if progress_callback is None:
                continue
            try:
                event = json.loads(line.strip())
            except (json.JSONDecodeError, TypeError):
                continue
            if (
                isinstance(event, Mapping)
                and event.get("event") == "TAG_REPORT_PROGRESS"
            ):
                try:
                    progress_callback(event)
                except Exception:
                    pass
    return_code = process.wait(timeout=4 * 60 * 60)
    stderr_thread.join()
    return subprocess.CompletedProcess(
        list(command), return_code,
        stdout="".join(stdout_parts), stderr="".join(stderr_parts),
    )


def _run_web_command(
    runner: Runner,
    command: Sequence[str],
    cwd: Path,
    progress_callback: ProgressCallback,
) -> subprocess.CompletedProcess[str]:
    try:
        parameters = inspect.signature(runner).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    if any(
        item.name == "progress_callback"
        or item.kind is inspect.Parameter.VAR_KEYWORD
        for item in parameters
    ):
        return runner(command, cwd, progress_callback=progress_callback)
    positional = [
        item for item in parameters
        if item.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    if len(positional) >= 3 or any(
        item.kind is inspect.Parameter.VAR_POSITIONAL for item in parameters
    ):
        return runner(command, cwd, progress_callback)
    return runner(command, cwd)


class JobQueue:
    def __init__(
        self,
        project_root: Path,
        config_path: Path,
        runner: Runner = _default_runner,
    ) -> None:
        self.project_root = project_root
        self.config_path = config_path
        self.runner = runner
        self._pending: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._worker = threading.Thread(target=self._work, name="tenable-web-queue", daemon=True)
        self._worker.start()

    def enqueue(self, client_ids: Sequence[str], request: Mapping[str, Any]) -> list[dict[str, Any]]:
        mode = str(request.get("mode") or "manual")
        if mode not in {"manual", "automatic"}:
            raise ValueError("Modo de execucao invalido.")
        days = request.get("days")
        start_at = str(request.get("start_at") or "").strip() or None
        end_at = str(request.get("end_at") or "").strip() or None
        if days not in (None, ""):
            days = int(days)
            if not 1 <= days <= 366:
                raise ValueError("Dias deve estar entre 1 e 366.")
        else:
            days = None
        if bool(start_at) != bool(end_at):
            raise ValueError("Informe inicio e fim do periodo.")
        if days is not None and start_at:
            raise ValueError("Use dias ou periodo especifico, nao os dois.")
        if mode == "automatic" and (days is not None or start_at):
            raise ValueError("O modo automatico sempre usa o mes anterior completo.")
        created: list[dict[str, Any]] = []
        with self._lock:
            busy = {
                item["client_id"] for item in self._jobs.values()
                if item["status"] in {"QUEUED", "RUNNING"}
            }
            for client_id in client_ids:
                if client_id in busy:
                    continue
                job_id = uuid.uuid4().hex
                job = {
                    "job_id": job_id,
                    "client_id": client_id,
                    "mode": mode,
                    "days": days,
                    "start_at": start_at,
                    "end_at": end_at,
                    "status": "QUEUED",
                    "progress": 8,
                    "created_at": _utc_now(),
                    "started_at": None,
                    "ended_at": None,
                    "error": None,
                    "run_id": None,
                    "tag_progress": None,
                    "warnings": [],
                }
                self._jobs[job_id] = job
                self._pending.put(job_id)
                created.append(dict(job))
                busy.add(client_id)
            self._trim()
        return created

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            pending = [item for item in self._jobs.values() if item["status"] == "QUEUED"]
            positions = {item["job_id"]: index + 1 for index, item in enumerate(pending)}
            result = []
            for item in sorted(self._jobs.values(), key=lambda row: row["created_at"], reverse=True):
                row = dict(item)
                row["queue_position"] = positions.get(item["job_id"])
                result.append(row)
            return result

    def retry(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            original = self._jobs.get(job_id)
            if original is None:
                raise KeyError("Trabalho não encontrado.")
            if original["status"] != "FAILED":
                raise ValueError("Somente trabalhos com falha podem ser reenfileirados.")
            request = {
                "mode": original["mode"],
                "days": original["days"],
                "start_at": original["start_at"],
                "end_at": original["end_at"],
            }
            client_id = original["client_id"]
        created = self.enqueue([client_id], request)
        if not created:
            raise ValueError("O cliente já está na fila ou em execução.")
        retried = created[0]
        with self._lock:
            self._jobs[retried["job_id"]]["retry_of_job_id"] = job_id
            retried = dict(self._jobs[retried["job_id"]])
        return retried

    def _trim(self) -> None:
        completed = [
            item for item in self._jobs.values() if item["status"] in {"COMPLETE", "FAILED"}
        ]
        for item in sorted(completed, key=lambda row: row["created_at"], reverse=True)[100:]:
            self._jobs.pop(item["job_id"], None)

    def _work(self) -> None:
        while True:
            job_id = self._pending.get()
            try:
                self._run(job_id)
            finally:
                self._pending.task_done()

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "RUNNING"
            job["progress"] = 45
            job["started_at"] = _utc_now()
            command = [
                sys.executable,
                "-m",
                "tenable_reports",
                "orchestrate",
                "--config",
                str(self.config_path),
                "--mode",
                job["mode"],
                "--client",
                job["client_id"],
                "--max-parallel",
                "1",
                "--confirm-live-api",
            ]
            if job["days"] is not None:
                command.extend(("--days", str(job["days"])))
            if job["start_at"]:
                command.extend(("--start-at", job["start_at"], "--end-at", job["end_at"]))
        try:
            def update_tag_progress(event: Mapping[str, Any]) -> None:
                current = int(event.get("current") or 0)
                total = max(1, int(event.get("total") or 0))
                with self._lock:
                    current_job = self._jobs.get(job_id)
                    if current_job is None:
                        return
                    current_job["tag_progress"] = {
                        "current": current,
                        "total": total,
                        "tag_uuid": str(event.get("tag_uuid") or ""),
                        "label": str(event.get("tag_label") or ""),
                    }
                    current_job["progress"] = min(
                        92, 45 + round(45 * current / total)
                    )

            completed = _run_web_command(
                self.runner, command, self.project_root, update_tag_progress
            )
            payload: dict[str, Any] = {}
            for line in reversed((completed.stdout or "").splitlines()):
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    payload = candidate
                    break
            with self._lock:
                job = self._jobs[job_id]
                job["ended_at"] = _utc_now()
                job["progress"] = 100
                job["run_id"] = payload.get("run_id")
                client_payloads = [
                    item.get("payload")
                    for item in payload.get("clients") or ()
                    if isinstance(item, Mapping)
                    and isinstance(item.get("payload"), Mapping)
                ]
                job["warnings"] = [
                    dict(warning)
                    for client_payload in client_payloads
                    for warning in client_payload.get("warnings") or ()
                    if isinstance(warning, Mapping)
                ]
                if completed.returncode == 0:
                    job["status"] = "COMPLETE"
                else:
                    job["status"] = "FAILED"
                    job["error"] = _safe_error(completed.stderr or completed.stdout)
        except Exception as exc:  # A fila nao pode morrer por causa de um cliente.
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "FAILED"
                job["progress"] = 100
                job["ended_at"] = _utc_now()
                job["error"] = _safe_error(str(exc))


class DashboardApplication:
    def __init__(
        self,
        *,
        project_root: Path,
        config_path: Path,
        runner: Runner = _default_runner,
        connection_checker: Callable[[Path], dict[str, Any]] = check_tenable_connection,
        tag_lister: Callable[[Path], Sequence[Mapping[str, Any]]] = list_tenable_tags,
        report_registry: ReportRegistry | None = None,
        backfill_state_provider: Callable[[], MainBackfillSourceState] | None = None,
        retention_state_provider: Callable[[], Mapping[str, Any]] | None = None,
        cleanup_status_recorder: Callable[..., Any] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = DashboardConfigStore(project_root=self.project_root, config_path=config_path)
        self.jobs = JobQueue(self.project_root, self.config.config_path, runner)
        self.connection_checker = connection_checker
        self.tag_lister = tag_lister
        self.database_error: str | None = None
        try:
            self.database: DashboardDatabase | None = DashboardDatabase(
                self.config.database_env_path()
            )
        except Exception as exc:
            self.database = None
            self.database_error = _safe_error(str(exc), limit=500)
        self.report_registry = report_registry
        if self.report_registry is None and self.database is not None:
            self.report_registry = PostgresReportRegistry(
                self.database.database, migrate=False
            )
        self._backfill_state_provider = backfill_state_provider
        if self._backfill_state_provider is None and self.database is not None:
            operations = PostgresOperationsRepository(self.database.database)
            self._backfill_state_provider = lambda: main_backfill_source_state(operations)
        self._backfill_lock = threading.RLock()
        self._retention_state_provider = retention_state_provider
        if self._retention_state_provider is None and self.database is not None:
            operations = PostgresOperationsRepository(self.database.database)
            self._retention_state_provider = operations.retention_state
        self._cleanup_status_recorder = cleanup_status_recorder
        if self._cleanup_status_recorder is None and self.database is not None:
            operations = PostgresOperationsRepository(self.database.database)
            self._cleanup_status_recorder = operations.record_cleanup_status

    def backfill_plan(self) -> MainBackfillPlan:
        if self.report_registry is None or self._backfill_state_provider is None:
            raise RuntimeError("Banco e registro de relatórios precisam estar disponíveis.")
        source_state = self._backfill_state_provider()
        return plan_main_backfill(
            self.report_registry.list_reports(include_deleted=True),
            used_history_run_ids=source_state.used_history_run_ids,
            existing_main_run_ids=source_state.existing_main_run_ids,
        )

    def apply_backfill(self, confirmation: str) -> dict[str, Any]:
        if confirmation != BACKFILL_CONFIRMATION:
            raise ValueError(
                f'Digite exatamente "{BACKFILL_CONFIRMATION}" para confirmar.'
            )
        with self._backfill_lock:
            plan = self.backfill_plan()
            applied: list[str] = []
            for key, run_id in plan.promotions:
                self.report_registry.promote_main(
                    key,
                    run_id,
                    actor="system-backfill",
                    reason="migração inicial",
                )
                applied.append(run_id)
            return {
                "applied_promotions": applied,
                "plan": plan.to_dict(),
            }

    def report_rows(
        self, client_id: str, *, include_deleted: bool = False
    ) -> list[dict[str, Any]]:
        if self.report_registry is None:
            return []
        documents_by_run: dict[str, list[dict[str, Any]]] = {}
        if self.database is not None:
            for document in self.database.reports(client_id):
                documents_by_run.setdefault(str(document.get("run_id") or ""), []).append(document)
        rows = []
        for report in self.report_registry.list_reports(
            client_id=client_id, include_deleted=include_deleted
        ):
            candidate = report.candidate
            key = reference_key_for_candidate(candidate)
            main = self.report_registry.get_main(key)
            documents = documents_by_run.get(candidate.run_id, [])
            rows.append({
                "run_id": candidate.run_id,
                "period_id": key.period_key,
                "period_start_at": candidate.period_start_at,
                "period_end_at": candidate.period_end_at,
                "origin": candidate.origin.value,
                "execution_type": candidate.execution_type,
                "status": candidate.publication_status,
                "is_main": bool(main and main.run_id == candidate.run_id),
                "deleted_at": report.deleted_at,
                "reference_run_id": None,
                "size_bytes": sum(int(item.get("size_bytes") or 0) for item in documents),
                "omitted_modules": [],
                "documents": documents,
            })
        return rows

    def storage_status(self) -> dict[str, Any]:
        payload = self.config.raw()
        raw_root = str((payload.get("defaults") or {}).get("output_root") or "../data")
        output_root = (self.config.config_path.parent / raw_root).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(output_root)
        active = sum(
            item["status"] in {"QUEUED", "RUNNING"} for item in self.jobs.snapshot()
        )
        by_client: dict[str, int] = {
            item["client_id"]: 0 for item in self.config.list_clients()
        }
        temporary_bytes = 0
        for scope in ("automatic-monthly", "manual"):
            for category in TRANSIENT_CATEGORIES:
                category_root = output_root / scope / category
                if not category_root.is_dir():
                    continue
                for path in category_root.rglob("*"):
                    if not path.is_file():
                        continue
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    temporary_bytes += size
                    relative = path.relative_to(category_root)
                    if relative.parts:
                        by_client.setdefault(relative.parts[0], 0)
                        by_client[relative.parts[0]] += size
        retention_state = (
            dict(self._retention_state_provider())
            if self._retention_state_provider is not None else {}
        )
        return {
            "path": str(output_root),
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "available_bytes": int(usage.free),
            "free_bytes": int(usage.free),
            "temporary_bytes": temporary_bytes,
            "pending_cleanup_runs": int(
                retention_state.get("pending_cleanup_runs") or 0
            ),
            "last_cleanup_at": retention_state.get("last_cleanup_at"),
            "last_cleanup_status": retention_state.get(
                "last_cleanup_status", "NEVER_RUN"
            ),
            "queue_reserved_bytes": active * required_free_bytes(last_success_bytes=None),
            "by_client": [
                {"client_id": client_id, "temporary_bytes": size}
                for client_id, size in sorted(by_client.items())
            ],
        }

    def cleanup_safe_residues(self, *, apply: bool = False) -> dict[str, Any]:
        if self._retention_state_provider is None:
            raise RuntimeError("Banco indisponível para validar as proteções de retenção.")
        config_payload = self.config.raw()
        defaults = (
            config_payload.get("defaults")
            if isinstance(config_payload.get("defaults"), Mapping) else {}
        )
        raw_output_root = str(defaults.get("output_root") or "../data")
        output_root = (self.config.config_path.parent / raw_output_root).resolve()
        state = dict(self._retention_state_provider())
        active_run_ids = {
            str(item.get("run_id") or "")
            for item in self.jobs.snapshot()
            if item.get("status") in {"QUEUED", "RUNNING"} and item.get("run_id")
        }
        active_run_ids.update(
            str(run_id)
            for run_id, status in dict(state.get("run_status") or {}).items()
            if str(status).upper() in {"QUEUED", "RUNNING"}
        )
        candidates: dict[Path, RetentionCandidate] = {}
        skipped: list[dict[str, Any]] = []
        policy = RetentionPolicy(
            failed_raw_days=int(
                defaults.get("failed_staging_days")
                or defaults.get("failed_raw_days")
                or 7
            ),
            successful_raw_days=int(defaults.get("successful_raw_days") or 60),
            normalized_days=int(defaults.get("normalized_days") or 90),
            documents_days=int(defaults.get("documents_days") or 395),
        )
        for scope in ("automatic-monthly", "manual"):
            scoped_root = output_root / scope
            plan = plan_tiered_retention(
                scoped_output_root=scoped_root,
                policy=policy,
                run_status=state.get("run_status"),
                history_confirmed_run_ids=state.get("history_confirmed_run_ids", ()),
                main_run_ids=state.get("main_run_ids", ()),
                active_run_ids=active_run_ids,
                retry_required_run_ids=state.get("retry_required_run_ids", ()),
            )
            for candidate in plan.candidates:
                candidates[candidate.path.resolve()] = candidate
            skipped.extend(item.to_dict() for item in plan.skipped)
            for cleanup_run in state.get("cleanup_runs") or ():
                if not isinstance(cleanup_run, Mapping):
                    continue
                run_id = str(cleanup_run.get("run_id") or "")
                client_id = str(cleanup_run.get("client_id") or "")
                if not run_id or not client_id:
                    continue
                if run_id in active_run_ids:
                    continue
                try:
                    immediate = plan_published_run_cleanup(
                        scoped_output_root=scoped_root,
                        client_id=client_id,
                        run_id=run_id,
                        publication_confirmed=True,
                        history_confirmed=(
                            run_id in set(state.get("history_confirmed_run_ids") or ())
                        ),
                    )
                except ValueError:
                    continue
                for candidate in immediate.candidates:
                    candidates[candidate.path.resolve()] = candidate
            log_plan = plan_orchestration_log_cleanup(
                scoped_output_root=scoped_root,
                retention_days=int(defaults.get("logs_days") or 90),
            )
            for candidate in log_plan.candidates:
                candidates[candidate.path.resolve()] = candidate

        ordered = tuple(candidates[path] for path in sorted(candidates, key=str))
        sizes: dict[Path, int] = {}
        for candidate in ordered:
            size = 0
            for item in candidate.path.rglob("*"):
                if item.is_file():
                    try:
                        size += item.stat().st_size
                    except OSError:
                        continue
            sizes[candidate.path.resolve()] = size
        candidate_payload = [{
            "category": item.category,
            "client_id": item.client_id,
            "run_id": item.run_id,
            "size_bytes": sizes.get(item.path.resolve(), 0),
            "reason": item.reason,
        } for item in ordered]
        removed: tuple[Path, ...] = ()
        failures = ()
        if apply:
            for scope in ("automatic-monthly", "manual"):
                scoped_root = output_root / scope
                scoped_candidates = tuple(
                    item for item in ordered
                    if item.path.resolve().is_relative_to(scoped_root.resolve())
                )
                cleanup_result = apply_cleanup_plan(
                    scoped_output_root=scoped_root,
                    candidates=scoped_candidates,
                )
                removed += cleanup_result.removed
                failures += cleanup_result.failures
        removed_bytes = sum(sizes.get(path.resolve(), 0) for path in removed)
        if apply and self._cleanup_status_recorder is not None:
            removed_set = {path.resolve() for path in removed}
            failed_set = {failure.path.resolve() for failure in failures}
            pending_run_ids = {
                str(item.get("run_id") or "")
                for item in state.get("cleanup_runs") or ()
                if isinstance(item, Mapping)
            }
            run_ids = pending_run_ids | {item.run_id for item in ordered}
            for run_id in sorted(run_ids - active_run_ids):
                run_candidates = [item for item in ordered if item.run_id == run_id]
                run_removed_bytes = sum(
                    sizes.get(item.path.resolve(), 0)
                    for item in run_candidates
                    if item.path.resolve() in removed_set
                )
                run_failed = any(
                    item.path.resolve() in failed_set for item in run_candidates
                )
                status = (
                    "PARTIAL" if run_failed and run_removed_bytes
                    else "FAILED" if run_failed
                    else "COMPLETE"
                )
                self._cleanup_status_recorder(
                    run_id, status, cleanup_bytes=run_removed_bytes
                )
        return {
            "applied": apply,
            "candidate_count": len(ordered),
            "candidate_bytes": sum(sizes.values()),
            "candidates": candidate_payload,
            "removed": [str(path) for path in removed],
            "removed_bytes": removed_bytes,
            "failed": [str(item.path) for item in failures],
            "skipped": skipped,
        }

    def check_connections(self, client_ids: Sequence[str]) -> list[dict[str, Any]]:
        clients = {item["client_id"]: item for item in self.config.list_clients()}
        unknown = [client_id for client_id in client_ids if client_id not in clients]
        if unknown:
            raise ValueError("Clientes nao encontrados: " + ", ".join(unknown))
        if not client_ids:
            raise ValueError("Nenhum cliente foi selecionado para o teste.")
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(4, len(client_ids))) as executor:
            future_map = {
                executor.submit(
                    self.connection_checker,
                    self.config.client_env_path(client_id),
                ): client_id
                for client_id in client_ids
            }
            for future in as_completed(future_map):
                client_id = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "ok": False,
                        "latency_ms": 0,
                        "message": _safe_error(str(exc), limit=300),
                        "checked_at": _utc_now(),
                    }
                results[client_id] = {
                    "client_id": client_id,
                    "display_name": clients[client_id]["display_name"],
                    **result,
                }
        return [results[client_id] for client_id in client_ids]

    def list_client_tags(self, client_id: str) -> dict[str, Any]:
        clients = {item["client_id"]: item for item in self.config.list_clients()}
        client = clients.get(client_id)
        if client is None:
            raise KeyError("Cliente nao encontrado.")
        discovered = self.tag_lister(self.config.client_env_path(client_id))
        saved = {
            str(item.get("tag_uuid") or ""): dict(item)
            for item in client.get("tag_reports") or ()
            if isinstance(item, Mapping) and str(item.get("tag_uuid") or "")
        }
        combined: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in discovered:
            tag_uuid = str(raw.get("tag_uuid") or raw.get("uuid") or "").strip()
            category_uuid = str(raw.get("category_uuid") or "").strip()
            category_name = str(raw.get("category_name") or "").strip()
            value = str(raw.get("value") or "").strip()
            if not tag_uuid or not category_name or not value or tag_uuid in seen:
                continue
            selected = saved.get(tag_uuid, {})
            combined.append({
                "tag_uuid": tag_uuid,
                "category_uuid": category_uuid,
                "category_name": category_name,
                "value": value,
                "generate_report": bool(selected.get("generate_report", False)),
                "include_temporal_comparison": bool(
                    selected.get("include_temporal_comparison", False)
                ),
                "available": True,
            })
            seen.add(tag_uuid)
        for tag_uuid, selected in saved.items():
            if tag_uuid in seen:
                continue
            combined.append({
                "tag_uuid": tag_uuid,
                "category_uuid": str(selected.get("category_uuid") or ""),
                "category_name": str(selected.get("category_name") or ""),
                "value": str(selected.get("value") or ""),
                "generate_report": bool(selected.get("generate_report", True)),
                "include_temporal_comparison": bool(
                    selected.get("include_temporal_comparison", False)
                ),
                "available": False,
            })
        combined.sort(key=lambda item: (
            str(item["category_name"]).casefold(),
            str(item["value"]).casefold(),
            str(item["tag_uuid"]),
        ))
        return {
            "client_id": client_id,
            "tag_reports_enabled": bool(client.get("tag_reports_enabled", False)),
            "tags": combined,
            "fetched_at": _utc_now(),
        }

    def state(self) -> dict[str, Any]:
        clients = self.config.list_clients()
        jobs = self.jobs.snapshot()
        summaries: dict[str, dict[str, Any]] = {}
        alerts: list[dict[str, Any]] = []
        database_error = self.database_error
        if self.database is not None:
            try:
                summaries = self.database.summaries()
                alerts = self.database.alerts()
                database_error = None
            except Exception as exc:
                database_error = _safe_error(str(exc), limit=500)
        latest_job: dict[str, dict[str, Any]] = {}
        for job in jobs:
            latest_job.setdefault(job["client_id"], job)
        latest_alert: dict[str, dict[str, Any]] = {}
        for alert in alerts:
            latest_alert.setdefault(str(alert.get("client_id") or ""), alert)
        for client in clients:
            client_id = client["client_id"]
            client["latest_report"] = summaries.get(client_id)
            client["job"] = latest_job.get(client_id)
            client["alert"] = latest_alert.get(client_id)
        return {
            "clients": clients,
            "jobs": jobs,
            "alerts": alerts,
            "database_error": database_error,
            "server_time": _utc_now(),
            "queue_mode": "sequential",
            "storage": self.storage_status(),
        }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "TenableReportsWeb/0.1"

    @property
    def app(self) -> DashboardApplication:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("[web] " + (format % args) + "\n")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_static("index.html", "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/static/"):
            name = parsed.path.removeprefix("/static/")
            if name not in {"app.css", "app.js"}:
                self._json_error(HTTPStatus.NOT_FOUND, "Arquivo nao encontrado.")
                return
            self._send_static(name, mimetypes.guess_type(name)[0] or "text/plain")
            return
        if parsed.path == "/api/state":
            self._json(HTTPStatus.OK, self.app.state())
            return
        if parsed.path == "/api/storage":
            try:
                self._json(HTTPStatus.OK, self.app.storage_status())
            except Exception as exc:
                self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(str(exc), limit=500))
            return
        if parsed.path == "/api/admin/backfill":
            try:
                self._json(HTTPStatus.OK, self.app.backfill_plan().to_dict())
            except RuntimeError as exc:
                self._json_error(HTTPStatus.SERVICE_UNAVAILABLE, _safe_error(str(exc), limit=500))
            except Exception as exc:
                self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(str(exc), limit=500))
            return
        match = re.fullmatch(r"/api/clients/([^/]+)/reports", parsed.path)
        if match:
            try:
                include_deleted = parse_qs(parsed.query).get("include_deleted", ["false"])[0].lower() == "true"
                reports = self.app.report_rows(
                    unquote(match.group(1)), include_deleted=include_deleted
                )
                self._json(HTTPStatus.OK, {"reports": reports})
            except Exception as exc:
                self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(str(exc), limit=500))
            return
        match = re.fullmatch(r"/api/clients/([^/]+)/tags", parsed.path)
        if match:
            try:
                self._json(
                    HTTPStatus.OK,
                    self.app.list_client_tags(unquote(match.group(1))),
                )
            except KeyError as exc:
                self._json_error(HTTPStatus.NOT_FOUND, _safe_error(str(exc), limit=300))
            except Exception as exc:
                status_code = int(getattr(exc, "status_code", 0) or 0)
                if status_code in {401, 403, 429}:
                    messages = {
                        401: "Credenciais Tenable invalidas ou expiradas.",
                        403: "A credencial nao possui permissao para consultar TAGs.",
                        429: "A Tenable limitou temporariamente a consulta de TAGs.",
                    }
                    self._json_error(HTTPStatus(status_code), messages[status_code])
                else:
                    self._json_error(
                        HTTPStatus.BAD_GATEWAY,
                        _safe_error(str(exc), limit=300),
                    )
            return
        match = re.fullmatch(r"/api/reports/(\d+)/download", parsed.path)
        if match:
            inline = parse_qs(parsed.query).get("inline", ["false"])[0].lower() == "true"
            self._download(int(match.group(1)), inline=inline)
            return
        self._json_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")

    def do_POST(self) -> None:  # noqa: N802
        if not self._write_allowed():
            return
        parsed = urlsplit(self.path)
        try:
            payload = self._request_json()
            if parsed.path == "/api/clients":
                client = self.app.config.add_client(payload)
                self._json(HTTPStatus.CREATED, {"client": client})
                return
            if parsed.path == "/api/jobs":
                known = {
                    item["client_id"] for item in self.app.config.list_clients() if item["enabled"]
                }
                requested = payload.get("client_ids") or sorted(known)
                if not isinstance(requested, list) or any(item not in known for item in requested):
                    raise ValueError("Ha clientes inexistentes ou desabilitados na selecao.")
                jobs = self.app.jobs.enqueue(requested, payload)
                if not jobs:
                    raise ValueError("Os clientes selecionados ja estao na fila ou em execucao.")
                self._json(HTTPStatus.ACCEPTED, {"jobs": jobs})
                return
            if parsed.path == "/api/connections/check":
                requested = payload.get("client_ids")
                if requested is None:
                    requested = [
                        item["client_id"]
                        for item in self.app.config.list_clients()
                        if item["enabled"]
                    ]
                if not isinstance(requested, list) or any(
                    not isinstance(item, str) for item in requested
                ):
                    raise ValueError("client_ids deve ser uma lista de IDs.")
                results = self.app.check_connections(requested)
                self._json(HTTPStatus.OK, {"results": results})
                return
            if parsed.path == "/api/admin/backfill/apply":
                result = self.app.apply_backfill(str(payload.get("confirmation") or ""))
                self._json(HTTPStatus.OK, result)
                return
            match = re.fullmatch(r"/api/reports/([^/]+)/main", parsed.path)
            if match:
                if self.app.report_registry is None:
                    self._json_error(HTTPStatus.SERVICE_UNAVAILABLE, "Registro de relatórios indisponível.")
                    return
                run_id = unquote(match.group(1))
                report = self.app.report_registry.get_report(run_id)
                main = self.app.report_registry.promote_main(
                    reference_key_for_candidate(report.candidate), run_id,
                    actor=str(payload.get("actor") or ""),
                    reason=str(payload.get("reason") or ""),
                )
                self._json(HTTPStatus.OK, {"run_id": main.run_id, "is_main": True})
                return
            match = re.fullmatch(r"/api/reports/([^/]+)/restore", parsed.path)
            if match:
                if self.app.report_registry is None:
                    self._json_error(HTTPStatus.SERVICE_UNAVAILABLE, "Registro de relatórios indisponível.")
                    return
                run_id = unquote(match.group(1))
                self.app.report_registry.restore(
                    run_id,
                    actor=str(payload.get("actor") or ""),
                    reason=str(payload.get("reason") or ""),
                )
                self._json(HTTPStatus.OK, {"run_id": run_id, "restored": True, "is_main": False})
                return
            match = re.fullmatch(r"/api/jobs/([^/]+)/retry", parsed.path)
            if match:
                job = self.app.jobs.retry(unquote(match.group(1)))
                self._json(HTTPStatus.ACCEPTED, {"job": job})
                return
            if parsed.path == "/api/storage/cleanup/preview":
                result = self.app.cleanup_safe_residues(apply=False)
                self._json(HTTPStatus.OK, result)
                return
            if parsed.path in {"/api/storage/cleanup/apply", "/api/storage/cleanup"}:
                result = self.app.cleanup_safe_residues(apply=True)
                self._json(HTTPStatus.OK, result)
                return
            self._json_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json_error(HTTPStatus.BAD_REQUEST, _safe_error(str(exc), limit=500))
        except RuntimeError as exc:
            self._json_error(HTTPStatus.SERVICE_UNAVAILABLE, _safe_error(str(exc), limit=500))
        except Exception as exc:
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(str(exc), limit=500))

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._write_allowed():
            return
        parsed = urlsplit(self.path)
        match = re.fullmatch(r"/api/reports/([^/]+)", parsed.path)
        if not match:
            self._json_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")
            return
        try:
            if self.app.report_registry is None:
                self._json_error(HTTPStatus.SERVICE_UNAVAILABLE, "Registro de relatórios indisponível.")
                return
            payload = self._request_json()
            run_id = unquote(match.group(1))
            self.app.report_registry.soft_delete(
                run_id,
                actor=str(payload.get("actor") or ""),
                reason=str(payload.get("reason") or ""),
                replacement_run_id=(
                    str(payload.get("replacement_run_id") or "").strip() or None
                ),
                allow_gap=bool(payload.get("allow_gap", False)),
            )
            self._json(HTTPStatus.OK, {"run_id": run_id, "deleted": True})
        except MainDeletionRequiresDecision as exc:
            self._json_error(HTTPStatus.CONFLICT, _safe_error(str(exc), limit=500))
        except KeyError as exc:
            self._json_error(HTTPStatus.NOT_FOUND, _safe_error(str(exc), limit=500))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json_error(HTTPStatus.BAD_REQUEST, _safe_error(str(exc), limit=500))
        except Exception as exc:
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(str(exc), limit=500))

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._write_allowed():
            return
        parsed = urlsplit(self.path)
        match = re.fullmatch(r"/api/clients/([^/]+)", parsed.path)
        if not match:
            self._json_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")
            return
        try:
            client = self.app.config.update_client(unquote(match.group(1)), self._request_json())
            self._json(HTTPStatus.OK, {"client": client})
        except KeyError as exc:
            self._json_error(HTTPStatus.NOT_FOUND, _safe_error(str(exc), limit=500))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json_error(HTTPStatus.BAD_REQUEST, _safe_error(str(exc), limit=500))
        except Exception as exc:
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(str(exc), limit=500))

    def _request_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length invalido.") from exc
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError("Corpo vazio ou acima do limite permitido.")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("O corpo JSON precisa ser um objeto.")
        return payload

    def _write_allowed(self) -> bool:
        if self.headers.get("X-Tenable-UI") != "1":
            self._json_error(HTTPStatus.FORBIDDEN, "Requisicao local invalida.")
            return False
        origin = self.headers.get("Origin")
        if origin and (urlsplit(origin).hostname or "").lower() not in LOOPBACK_HOSTS:
            self._json_error(HTTPStatus.FORBIDDEN, "Origem nao autorizada.")
            return False
        return True

    def _download(self, document_id: int, *, inline: bool = False) -> None:
        if self.app.database is None:
            self._json_error(HTTPStatus.SERVICE_UNAVAILABLE, "Banco indisponivel.")
            return
        try:
            path = self.app.database.document(document_id)
        except Exception as exc:
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(str(exc), limit=500))
            return
        if path is None or not path.is_file() or path.suffix.lower() not in {".docx", ".pdf"}:
            self._json_error(HTTPStatus.NOT_FOUND, "Documento nao encontrado no disco.")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        disposition = "inline" if inline else "attachment"
        self.send_header("Content-Disposition", f'{disposition}; filename="{path.name.replace(chr(34), "")}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                self.wfile.write(chunk)

    def _send_static(self, name: str, content_type: str) -> None:
        path = STATIC_DIRECTORY / name
        if not path.is_file():
            self._json_error(HTTPStatus.NOT_FOUND, "Interface nao encontrada.")
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _json_error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message})


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: DashboardApplication) -> None:
        super().__init__(address, DashboardHandler)
        self.app = app


def serve_dashboard(
    *,
    project_root: str | Path,
    config_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("O MVP web aceita somente host local (127.0.0.1 ou localhost).")
    root = Path(project_root).resolve()
    config = Path(config_path)
    if not config.is_absolute():
        config = root / config
    app = DashboardApplication(project_root=root, config_path=config)
    server = DashboardHTTPServer((host, port), app)
    url = f"http://{host}:{port}"
    print(json.dumps({
        "status": "ready",
        "url": url,
        "config": str(app.config.config_path),
        "queue_mode": "sequential",
    }, ensure_ascii=False))
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
