from __future__ import annotations

import json
import inspect
import mimetypes
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, quote, unquote, urlsplit

from tenable_reports.application.execution_control import FileExecutionControl
from tenable_reports.application.web_batches import (
    BatchClientConflictError,
    DerivedBatchRequest,
    WebBatchRepository,
    build_manual_batch_options,
)
from tenable_reports.application.cloud_contract import probe_cloud_contract
from tenable_reports.application.orchestration import SAFE_ID_PATTERN
from tenable_reports.application.orchestration import (
    load_orchestration_config,
    resolve_remote_worker_capacity,
)
from tenable_reports.application.postgresql_migration import (
    MainBackfillSourceState,
    main_backfill_source_state,
)
from tenable_reports.application.publishing import write_json_atomic
from tenable_reports.application.report_main_backfill import (
    MainBackfillPlan,
    plan_main_backfill,
)
from tenable_reports.application.report_archives import (
    ArchiveClient,
    ArchiveDocument,
    ArchiveReportSet,
    EmptyReportArchiveError,
    InsufficientReportArchiveSpace,
    ReportArchiveResult,
    UnsafeReportArchivePath,
    build_monthly_report_archive,
    build_report_set_archive,
)
from tenable_reports.application.report_registry import (
    MainDeletionRequiresDecision,
    ReportRegistry,
)
from tenable_reports.application.report_components import ReportComponentRepository
from tenable_reports.application.report_set_purge import (
    ActiveReportSetError,
    MainReportReplacementRequired,
    ReportSetPurgeFinalizationError,
    ReportSetPurgeService,
    UnsafeReportSetPath,
)
from tenable_reports.application.storage_guard import required_free_bytes
from tenable_reports.application.vm_export_policy import recovery_vm_strategy
from tenable_reports.application.was_recovery import (
    WasRecoveryDecision,
    WasRecoveryRecord,
    WasRecoveryStatus,
)
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
from tenable_reports.config.analysts import (
    AnalystCatalog,
    AnalystInUseError,
    AnalystRecord,
)
from tenable_reports.config.environment import (
    CloudCredentialConfig,
    CredentialConfig,
    load_dotenv_file,
)
from tenable_reports.config.profile import ClientProfile
from tenable_reports.infrastructure.cloud_snapshots_postgresql import (
    PostgresCloudSnapshotRepository,
)
from tenable_reports.infrastructure.postgresql import (
    PostgresDatabase,
    PostgresOperationsRepository,
    SCHEMA_NAME,
)
from tenable_reports.infrastructure.report_registry_postgresql import PostgresReportRegistry
from tenable_reports.infrastructure.report_components_postgresql import (
    PostgresReportComponentRepository,
)
from tenable_reports.infrastructure.report_set_purge_postgresql import (
    PostgresReportSetPurgeRepository,
)
from tenable_reports.infrastructure.was_recovery_postgresql import (
    PostgresWasRecoveryRepository,
)
from tenable_reports.infrastructure.web_batches_postgresql import (
    PostgresWebBatchRepository,
)
from tenable_reports.domain.report_reference import reference_key_for_candidate
from tenable_reports.domain.report_components import (
    ComponentAttempt,
    ComponentStatus,
    ReportComponent,
    summarize_component_set,
)
from tenable_reports.domain.web_batches import BatchAction
from tenable_reports.infrastructure.tenable_cloud.client import (
    CloudGraphQLClient,
    CloudGraphQLConfig,
)
from tenable_reports.infrastructure.tenable_vm.client import (
    ExportTimeoutError,
    TenableVmClient,
    TenableVmConfig,
)
from tenable_reports.webapp.durable_dashboard_queue import (
    DurableDashboardJobQueue,
)


STATIC_DIRECTORY = Path(__file__).with_name("static")
MAX_REQUEST_BYTES = 64 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
PREPARED_ARCHIVE_TTL_SECONDS = 5 * 60
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


def _job_error_from_result(
    payload: Mapping[str, Any],
    completed: subprocess.CompletedProcess[str],
) -> str:
    candidates: list[Any] = []
    for client in payload.get("clients") or ():
        if not isinstance(client, Mapping):
            continue
        candidates.append(client.get("error"))
        attempts = client.get("attempts") or ()
        if attempts and isinstance(attempts[-1], Mapping):
            candidates.append(attempts[-1].get("error"))
    candidates.extend((
        payload.get("message"),
        payload.get("error"),
        completed.stderr,
        completed.stdout,
    ))
    selected = next(
        (
            str(candidate)
            for candidate in candidates
            if candidate is not None and str(candidate).strip()
        ),
        "Falha sem mensagem detalhada.",
    )
    return _safe_error(selected)


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


def _cloud_token_status(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        values = _read_env_values(path)
    except OSError:
        return False
    return bool(values.get("TCS_API_SECRET") or values.get("TCS_API_KEY"))


def _write_env_values(path: Path, values: Mapping[str, str]) -> None:
    preferred = (
        "TENABLE_BASE_URL",
        "TENABLE_ACCESS",
        "TENABLE_SECRET",
        "TENABLE_HTTP_TIMEOUT_SECONDS",
        "TENABLE_VALIDATE_TLS",
        "TENABLE_EXPORT_POLL_SECONDS",
        "TENABLE_EXPORT_MAX_POLL_SECONDS",
        "TENABLE_EXPORT_QUEUE_TIMEOUT_SECONDS",
        "TENABLE_EXPORT_PROCESSING_TIMEOUT_SECONDS",
        "TENABLE_EXPORT_STALL_WARNING_SECONDS",
        "TCS_API_SECRET",
        "TCS_HTTP_TIMEOUT_SECONDS",
        "TCS_HTTP_RETRIES",
        "TCS_CA_BUNDLE",
    )
    ordered = [key for key in preferred if key in values]
    ordered.extend(sorted(key for key in values if key not in preferred))
    path.write_text(
        "\n".join(f"{key}={values[key]}" for key in ordered) + "\n",
        encoding="utf-8",
    )


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


def check_cloud_connection(path: Path, environment: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        credentials = CloudCredentialConfig.from_environment(_read_env_values(path))
        if not credentials.is_complete:
            raise ValueError("Token Tenable Cloud Security nao configurado.")

        def client_factory(endpoint: str) -> CloudGraphQLClient:
            return CloudGraphQLClient(CloudGraphQLConfig(
                endpoint=endpoint,
                api_secret=credentials.api_secret,
                timeout_seconds=credentials.timeout_seconds,
                retries=credentials.retries,
                ca_bundle=credentials.ca_bundle,
            ))

        report = probe_cloud_contract(
            environment,
            client_factory=client_factory,
        )
        unavailable = [
            item.name for item in report.sources if item.status != "AVAILABLE"
        ]
    except Exception as exc:  # O resultado seguro deve voltar para a interface.
        return {
            "ok": False,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "message": _safe_error(str(exc), limit=300),
            "checked_at": _utc_now(),
            "retryable": bool(getattr(exc, "retryable", False)),
        }
    message = "Conexao com a API Tenable Cloud Security funcionando."
    if unavailable:
        message += " Fontes opcionais indisponiveis: " + ", ".join(unavailable) + "."
    return {
        "ok": bool(report.required_ready),
        "latency_ms": round((time.monotonic() - started) * 1000),
        "message": message,
        "checked_at": report.checked_at,
        "retryable": False,
        "required_ready": bool(report.required_ready),
        "sources": {
            item.name: item.status for item in report.sources
        },
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


def cancel_tenable_export(path: Path, export_uuid: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}", export_uuid):
        raise ValueError("UUID de export VM invalido.")
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
    result = client.cancel_vulnerability_export(export_uuid)
    return {
        "export_uuid": export_uuid,
        "status": str(result.get("status") or "CANCELLED"),
    }


class DashboardConfigStore:
    """Gerencia a carteira sem expor o conteudo dos arquivos de credenciais."""

    def __init__(self, *, project_root: Path, config_path: Path) -> None:
        self.project_root = project_root.resolve()
        self.config_path = config_path.resolve()
        self._lock = threading.RLock()
        self.analysts = AnalystCatalog(
            self.project_root / "orchestration" / "analysts.json"
        )
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
                "cloud_template": _relative_path(
                    self.project_root / "templates" / "corporate" / "cloud-base-v1.docx",
                    self.config_path.parent,
                ),
                "database_env_file": _relative_path(
                    self.project_root / "credentials" / "database.env",
                    self.config_path.parent,
                ),
                "max_parallel": 1,
                "remote_collection_workers": 0,
                "local_build_workers": 1,
                "remote_processing_timeout_seconds": 7200,
                "remote_progress_warning_seconds": 900,
                "max_clients_per_batch": 64,
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

    @staticmethod
    def _analyst_payload(record: AnalystRecord) -> dict[str, Any]:
        return {
            "analyst_id": record.analyst_id,
            "display_name": record.display_name,
            "active": record.active,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }

    def list_analysts(self) -> list[dict[str, Any]]:
        return [self._analyst_payload(record) for record in self.analysts.list()]

    def create_analyst(self, values: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            created = self.analysts.create(
                display_name=str(values.get("display_name") or "")
            )
            return self._analyst_payload(created)

    def update_analyst(
        self,
        analyst_id: str,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            current = self.analysts.get(analyst_id)
            if current is None:
                raise ValueError("Analista não encontrado.")
            if "display_name" in values and not isinstance(
                values["display_name"], str
            ):
                raise ValueError("display_name deve ser texto.")
            if "active" in values and not isinstance(values["active"], bool):
                raise ValueError("active deve ser booleano.")
            updated = self.analysts.update(
                current.analyst_id,
                display_name=values.get("display_name", current.display_name),
                active=values.get("active", current.active),
            )
            return self._analyst_payload(updated)

    def delete_analyst(self, analyst_id: str) -> None:
        with self._lock:
            self.analysts.delete(analyst_id, is_in_use=self._analyst_in_use)

    def _analyst_in_use(self, analyst_id: str) -> bool:
        payload = self.raw()
        for client in payload.get("clients") or []:
            if not isinstance(client, Mapping):
                continue
            profile_path = (
                self.config_path.parent / str(client.get("profile") or "")
            ).resolve()
            try:
                profile = _read_json(profile_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise AnalystInUseError(
                    "Não foi possível verificar todos os vínculos de clientes."
                ) from exc
            if (
                self._optional_analyst_id(profile.get("responsible_analyst_id"))
                == analyst_id
            ):
                return True
        return False

    @staticmethod
    def _optional_analyst_id(value: Any) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    def _validate_responsible_analyst_id(
        self,
        value: Any,
        *,
        current_id: str | None = None,
    ) -> str | None:
        analyst_id = self._optional_analyst_id(value)
        if analyst_id is None:
            return None
        analyst = self.analysts.get(analyst_id)
        if analyst is None:
            raise ValueError("Analista responsável não encontrado.")
        if not analyst.active and analyst_id != current_id:
            raise ValueError("Analista responsável está inativo.")
        return analyst_id

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

    def client_profile_path(self, client_id: str) -> Path:
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
        return (self.config_path.parent / str(client.get("profile") or "")).resolve()

    def list_clients(self) -> list[dict[str, Any]]:
        payload = self.raw()
        analysts_by_id = {
            record.analyst_id: record for record in self.analysts.list()
        }
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
            reporting = (
                profile.get("reporting")
                if isinstance(profile.get("reporting"), Mapping) else {}
            )
            vm_export = (
                reporting.get("vm_export")
                if isinstance(reporting.get("vm_export"), Mapping) else {}
            )
            cloud_scope = (
                (profile.get("scope") or {}).get("cloud_security")
                if isinstance((profile.get("scope") or {}).get("cloud_security"), Mapping)
                else {}
            )
            responsible_analyst_id = self._optional_analyst_id(
                profile.get("responsible_analyst_id")
            )
            responsible_analyst = (
                analysts_by_id.get(responsible_analyst_id)
                if responsible_analyst_id is not None
                else None
            )
            result.append({
                "client_id": client_id,
                "display_name": str(
                    raw.get("display_name") or profile.get("display_name") or client_id
                ),
                "tenant_id": str(profile.get("tenant_id") or ""),
                "responsible_analyst_id": responsible_analyst_id,
                "responsible_analyst_name": (
                    responsible_analyst.display_name if responsible_analyst else None
                ),
                "responsible_analyst_active": bool(
                    responsible_analyst and responsible_analyst.active
                ),
                "enabled": bool(raw.get("enabled", True)),
                "tags": list(raw.get("tags") or []),
                "profile_exists": profile_path.is_file(),
                "profile_error": profile_error,
                "env_exists": env_exists,
                "credentials_ready": credentials_ready,
                "was_enabled": bool(((profile.get("scope") or {}).get("was") or {}).get("enabled")),
                "cloud_enabled": bool(cloud_scope.get("enabled")),
                "cloud_token_saved": _cloud_token_status(env_path),
                "cloud_environment": str(cloud_scope.get("environment") or "global"),
                "cloud_layout": "expanded",
                "intelligence_enabled": bool(report.get("intelligence_modules") or []),
                "include_output": bool(raw.get("include_output", False)),
                "show_source_filters": bool(presentation.get("show_source_filters", False)),
                "vm_export_strategy": str(vm_export.get("strategy") or "combined"),
                "vm_num_assets_per_chunk": int(
                    vm_export.get("num_assets_per_chunk") or 1000
                ),
                "vm_selective_properties": str(
                    vm_export.get("selective_properties") or "disabled"
                ),
                "historical_source": str(
                    vm_export.get("historical_source") or "legacy"
                ),
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
            responsible_analyst_id = self._validate_responsible_analyst_id(
                values.get("responsible_analyst_id")
            )
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
            cloud_secret = self._secret(
                values.get("cloud_api_secret"), "Token Cloud Security"
            )
            cloud_environment = str(
                values.get("cloud_environment") or "global"
            ).strip().lower()
            cloud_layout = "expanded"
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
                "responsible_analyst_id": responsible_analyst_id,
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
                    "cloud_security": {
                        "enabled": cloud_enabled,
                        "environment": cloud_environment,
                        "layout": cloud_layout,
                    },
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
                    "vm_export": {
                        "strategy": str(
                            values.get("vm_export_strategy") or "combined"
                        ).strip().lower(),
                        "num_assets_per_chunk": int(
                            values.get("vm_num_assets_per_chunk") or 1000
                        ),
                        "selective_properties": str(
                            values.get("vm_selective_properties") or "disabled"
                        ).strip().lower(),
                        "historical_source": str(
                            values.get("historical_source") or "legacy"
                        ).strip().lower(),
                        "historical_fallback": "warn_legacy",
                    },
                },
            }
            ClientProfile.from_dict(profile)
            write_json_atomic(profile_path, profile)
            _write_env_values(env_path, {
                "TENABLE_BASE_URL": "https://cloud.tenable.com",
                "TENABLE_ACCESS": access_key,
                "TENABLE_SECRET": secret_key,
                "TENABLE_HTTP_TIMEOUT_SECONDS": "30",
                "TENABLE_VALIDATE_TLS": "true",
                "TENABLE_EXPORT_POLL_SECONDS": "10",
                "TENABLE_EXPORT_MAX_POLL_SECONDS": "30",
                "TENABLE_EXPORT_QUEUE_TIMEOUT_SECONDS": "1800",
                "TENABLE_EXPORT_PROCESSING_TIMEOUT_SECONDS": "7200",
                "TENABLE_EXPORT_STALL_WARNING_SECONDS": "1800",
                "TCS_API_SECRET": cloud_secret,
                "TCS_HTTP_TIMEOUT_SECONDS": "180",
                "TCS_HTTP_RETRIES": "4",
            })
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
            if "responsible_analyst_id" in values:
                current_analyst_id = self._optional_analyst_id(
                    profile.get("responsible_analyst_id")
                )
                profile["responsible_analyst_id"] = (
                    self._validate_responsible_analyst_id(
                        values.get("responsible_analyst_id"),
                        current_id=current_analyst_id,
                    )
                )
                profile_changed = True
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
            vm_fields = {
                "vm_export_strategy",
                "vm_num_assets_per_chunk",
                "vm_selective_properties",
                "historical_source",
            }
            if vm_fields.intersection(values):
                reporting = profile.setdefault("reporting", {})
                vm_export = reporting.setdefault("vm_export", {})
                if "vm_export_strategy" in values:
                    vm_export["strategy"] = str(
                        values["vm_export_strategy"]
                    ).strip().lower()
                if "vm_num_assets_per_chunk" in values:
                    vm_export["num_assets_per_chunk"] = int(
                        values["vm_num_assets_per_chunk"]
                    )
                if "vm_selective_properties" in values:
                    vm_export["selective_properties"] = str(
                        values["vm_selective_properties"]
                    ).strip().lower()
                if "historical_source" in values:
                    vm_export["historical_source"] = str(
                        values["historical_source"]
                    ).strip().lower()
                    vm_export.setdefault("historical_fallback", "warn_legacy")
                ClientProfile.from_dict(profile)
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
            capability_fields = {
                "was_enabled",
                "cloud_enabled",
                "cloud_environment",
                "intelligence_enabled",
            }
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
                cloud_scope["environment"] = str(
                    values.get("cloud_environment", cloud_scope.get("environment", "global"))
                ).strip().lower()
                cloud_scope["layout"] = "expanded"
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
            cloud_secret = self._secret(
                values.get("cloud_api_secret"), "Token Cloud Security"
            )
            if access_key or secret_key or cloud_secret:
                if bool(access_key) != bool(secret_key):
                    raise ValueError("Para trocar credenciais VM, informe as duas chaves.")
                env_path = (self.config_path.parent / str(client["env_file"])).resolve()
                env_values = _read_env_values(env_path) if env_path.is_file() else {}
                if access_key and secret_key:
                    env_values["TENABLE_ACCESS"] = access_key
                    env_values["TENABLE_SECRET"] = secret_key
                if cloud_secret:
                    env_values["TCS_API_SECRET"] = cloud_secret
                    env_values.pop("TCS_API_KEY", None)
                env_values.setdefault("TCS_HTTP_TIMEOUT_SECONDS", "180")
                env_values.setdefault("TCS_HTTP_RETRIES", "4")
                _write_env_values(env_path, env_values)
            if profile_changed:
                ClientProfile.from_dict(profile)
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
                   d.document_kind, d.document_variant,
                   d.tag_uuid, d.tag_category, d.tag_value
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
            "document_variant": row[10],
            "tag_uuid": row[11],
            "tag_category": row[12],
            "tag_value": row[13],
        } for row in rows]

    def report_documents(self, run_id: str) -> list[dict[str, Any]]:
        query = f"""
            select d.document_id, d.path, d.size_bytes, d.package_status,
                   r.run_id, r.period_id, r.execution_type, r.ended_at, p.created_at,
                   d.document_kind, d.document_variant,
                   d.tag_uuid, d.tag_category, d.tag_value
            from {SCHEMA_NAME}.published_documents d
            join {SCHEMA_NAME}.publications p on p.publication_id = d.publication_id
            join {SCHEMA_NAME}.report_runs r on r.run_id = p.run_id
            where r.run_id = %s
            order by d.document_id
        """
        with self.database.connection() as connection:
            rows = connection.execute(query, (run_id,)).fetchall()
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
            "document_variant": row[10],
            "tag_uuid": row[11],
            "tag_category": row[12],
            "tag_value": row[13],
        } for row in rows]

    def cloud_results(self, client_id: str) -> dict[str, dict[str, Any]]:
        query = f"""
            select distinct on (payload ->> 'run_id')
                   payload ->> 'run_id', payload ->> 'cloud_status',
                   coalesce(payload -> 'cloud_warnings', '[]'::jsonb)
            from {SCHEMA_NAME}.orchestration_clients
            where client_id = %s
              and payload is not null
              and nullif(payload ->> 'run_id', '') is not null
              and nullif(payload ->> 'cloud_status', '') is not null
            order by payload ->> 'run_id',
                     coalesce(ended_at, started_at) desc nulls last,
                     orchestration_client_id desc
        """
        with self.database.connection() as connection:
            rows = connection.execute(query, (client_id,)).fetchall()
        return {
            str(row[0]): {
                "status": str(row[1] or "UNKNOWN"),
                "warnings": [
                    dict(item) for item in (row[2] or ())
                    if isinstance(item, Mapping)
                ],
            }
            for row in rows
            if row[0]
        }

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
ProgressSink = Callable[[str, Mapping[str, Any]], None]
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _terminate_local_process_tree(process: subprocess.Popen[str]) -> bool:
    """Terminate only the local child tree; never call a remote API."""

    if process.poll() is not None:
        return False
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if process.poll() is None:
            process.kill()
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return True


def _default_runner(
    command: Sequence[str],
    cwd: Path,
    progress_callback: ProgressCallback | None = None,
    cancellation_probe: Callable[[], bool] | None = None,
    process_started_callback: Callable[[int], None] | None = None,
    fallback_callback: Callable[[int], None] | None = None,
    stop_grace_seconds: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    child_environment = os.environ.copy()
    child_environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    process_options: dict[str, Any] = {}
    if os.name == "nt":
        process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_options["start_new_session"] = True
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=child_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **process_options,
    )
    if process_started_callback is not None:
        try:
            process_started_callback(process.pid)
        except Exception:
            pass
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    monitor_done = threading.Event()

    def drain_stderr() -> None:
        if process.stderr is not None:
            stderr_parts.extend(process.stderr)

    def monitor_stop_request() -> None:
        if cancellation_probe is None:
            return
        while not monitor_done.wait(0.2):
            try:
                requested = cancellation_probe()
            except Exception:
                requested = False
            if not requested:
                continue
            deadline = time.monotonic() + max(0.0, float(stop_grace_seconds))
            while process.poll() is None and time.monotonic() < deadline:
                if monitor_done.wait(0.1):
                    return
            if process.poll() is None and _terminate_local_process_tree(process):
                if fallback_callback is not None:
                    try:
                        fallback_callback(process.pid)
                    except Exception:
                        pass
            return

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stop_thread = threading.Thread(target=monitor_stop_request, daemon=True)
    stderr_thread.start()
    stop_thread.start()
    try:
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
                    and event.get("event") in {
                        "TAG_REPORT_PROGRESS",
                        "TENABLE_EXPORT_PROGRESS",
                        "TENABLE_CLOUD_PROGRESS",
                        "TENABLE_EXPORT_NO_PROGRESS_WARNING",
                    }
                ):
                    try:
                        progress_callback(event)
                    except Exception:
                        pass
        return_code = process.wait(timeout=4 * 60 * 60)
    finally:
        monitor_done.set()
        stop_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
    return subprocess.CompletedProcess(
        list(command), return_code,
        stdout="".join(stdout_parts), stderr="".join(stderr_parts),
    )


def _run_web_command(
    runner: Runner,
    command: Sequence[str],
    cwd: Path,
    progress_callback: ProgressCallback,
    *,
    cancellation_probe: Callable[[], bool] | None = None,
    process_started_callback: Callable[[int], None] | None = None,
    fallback_callback: Callable[[int], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        parameters = tuple(inspect.signature(runner).parameters.values())
    except (TypeError, ValueError):
        parameters = ()
    names = {item.name for item in parameters}
    accepts_keywords = any(
        item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters
    )
    optional_values = {
        "progress_callback": progress_callback,
        "cancellation_probe": cancellation_probe,
        "process_started_callback": process_started_callback,
        "fallback_callback": fallback_callback,
    }
    keyword_arguments = {
        name: value
        for name, value in optional_values.items()
        if accepts_keywords or name in names
    }
    if keyword_arguments:
        return runner(command, cwd, **keyword_arguments)
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
        *,
        start_worker: bool = True,
        progress_sink: ProgressSink | None = None,
    ) -> None:
        self.project_root = project_root
        self.config_path = config_path
        self.runner = runner
        self.progress_sink = progress_sink
        self.process_sink: Callable[[str, int], None] | None = None
        self.fallback_sink: Callable[[str, int], None] | None = None
        self._pending: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(
                target=self._work,
                name="tenable-web-queue",
                daemon=True,
            )
            self._worker.start()

    def enqueue(self, client_ids: Sequence[str], request: Mapping[str, Any]) -> list[dict[str, Any]]:
        mode = str(request.get("mode") or "manual")
        if mode not in {"manual", "automatic"}:
            raise ValueError("Modo de execucao invalido.")
        vm_selective_mode = (
            str(request.get("vm_selective_mode") or "").strip().lower() or None
        )
        if vm_selective_mode not in {None, "disabled", "validation", "enabled"}:
            raise ValueError("Modo seletivo VM invalido.")
        vm_export_strategy = (
            str(request.get("vm_export_strategy") or "").strip().lower() or None
        )
        if vm_export_strategy not in {None, "combined", "split"}:
            raise ValueError("Estrategia de export VM invalida.")
        vm_export_uuid = (
            str(request.get("vm_export_uuid") or "").strip() or None
        )
        if vm_export_uuid:
            try:
                uuid.UUID(vm_export_uuid)
            except ValueError as exc:
                raise ValueError("UUID de export VM invalido.") from exc
        historical_source = (
            str(request.get("historical_source") or "").strip().lower() or None
        )
        if historical_source not in {None, "legacy", "inventory-beta"}:
            raise ValueError("Fonte historica invalida.")
        was_failure_policy = (
            str(request.get("was_failure_policy") or "").strip().lower()
            or None
        )
        if was_failure_policy not in {
            None,
            "wait",
            "continue",
            "retry_then_continue",
        }:
            raise ValueError("Politica de falha WAS invalida.")
        force_live_collection = request.get("force_live_collection") is True
        days = request.get("days")
        start_at = str(request.get("start_at") or "").strip() or None
        end_at = str(request.get("end_at") or "").strip() or None
        start_date = str(request.get("start_date") or "").strip() or None
        end_date = str(request.get("end_date") or "").strip() or None
        if bool(start_date) != bool(end_date):
            raise ValueError("Informe a data inicial e a data final.")
        if start_date:
            if start_at or end_at:
                raise ValueError(
                    "Use datas inclusivas ou instantes especificos, nao os dois."
                )
            try:
                parsed_start_date = date.fromisoformat(start_date)
                parsed_end_date = date.fromisoformat(end_date)
            except ValueError as exc:
                raise ValueError(
                    "Informe datas validas no formato AAAA-MM-DD."
                ) from exc
            if parsed_end_date < parsed_start_date:
                raise ValueError("Data final nao pode ser anterior a data inicial.")
            start_at = parsed_start_date.isoformat()
            end_at = (parsed_end_date + timedelta(days=1)).isoformat()
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
                    "operation": "report",
                    "mode": mode,
                    "days": days,
                    "start_at": start_at,
                    "end_at": end_at,
                    "vm_selective_mode": vm_selective_mode,
                    "vm_export_strategy": vm_export_strategy,
                    "vm_export_uuid": vm_export_uuid,
                    "historical_source": historical_source,
                    "was_failure_policy": was_failure_policy,
                    "force_live_collection": force_live_collection,
                    "confirm_historical_reconstruction": bool(
                        request.get("confirm_historical_reconstruction", False)
                    ),
                    "status": "QUEUED",
                    "progress": 8,
                    "created_at": _utc_now(),
                    "started_at": None,
                    "ended_at": None,
                    "error": None,
                    "run_id": None,
                    "tag_progress": None,
                    "export_progress": None,
                    "was_export_progress": None,
                    "was_recovery": None,
                    "cloud_progress": None,
                    "cloud_status": None,
                    "warnings": [],
                    "vm_export_validation": None,
                    "collection_route": None,
                    "reconstruction_status": None,
                    "collection_sources": [],
                }
                self._jobs[job_id] = job
                self._pending.put(job_id)
                created.append(dict(job))
                busy.add(client_id)
            self._trim()
        return created

    def enqueue_cloud_retry(
        self,
        *,
        run_id: str,
        client_id: str,
        profile_path: Path,
        env_path: Path,
        database_env_path: Path,
        cloud_template_path: Path,
    ) -> dict[str, Any]:
        with self._lock:
            if any(
                item["client_id"] == client_id
                and item["status"] in {"QUEUED", "RUNNING"}
                for item in self._jobs.values()
            ):
                raise ValueError("O cliente ja esta na fila ou em execucao.")
            job_id = uuid.uuid4().hex
            job = {
                "job_id": job_id,
                "client_id": client_id,
                "operation": "cloud_retry",
                "mode": "manual",
                "days": None,
                "start_at": None,
                "end_at": None,
                "vm_selective_mode": None,
                "vm_export_strategy": None,
                "historical_source": None,
                "force_live_collection": False,
                "confirm_historical_reconstruction": False,
                "status": "QUEUED",
                "progress": 8,
                "created_at": _utc_now(),
                "started_at": None,
                "ended_at": None,
                "error": None,
                "run_id": run_id,
                "tag_progress": None,
                "export_progress": None,
                "was_export_progress": None,
                "was_recovery": None,
                "cloud_progress": None,
                "cloud_status": "RETRY_QUEUED",
                "warnings": [],
                "vm_export_validation": None,
                "collection_route": None,
                "reconstruction_status": None,
                "collection_sources": [],
                "_profile_path": str(profile_path),
                "_env_path": str(env_path),
                "_database_env_path": str(database_env_path),
                "_cloud_template_path": str(cloud_template_path),
            }
            self._jobs[job_id] = job
            self._pending.put(job_id)
            self._trim()
            return {
                key: value for key, value in job.items() if not key.startswith("_")
            }

    def enqueue_was_recovery(
        self,
        *,
        run_id: str,
        client_id: str,
        decision: WasRecoveryDecision,
        checkpoint_path: Path,
        profile_path: Path,
        env_path: Path,
        database_env_path: Path,
        template_path: Path,
        assets_dir: Path,
    ) -> dict[str, Any]:
        with self._lock:
            if any(
                item["client_id"] == client_id
                and item["status"] in {"QUEUED", "RUNNING"}
                for item in self._jobs.values()
            ):
                raise ValueError("O cliente ja esta na fila ou em execucao.")
            retry = decision is WasRecoveryDecision.RETRY_WAS
            job_id = uuid.uuid4().hex
            job = {
                "job_id": job_id,
                "client_id": client_id,
                "operation": "was_retry" if retry else "was_continue",
                "mode": "manual",
                "days": None,
                "start_at": None,
                "end_at": None,
                "vm_selective_mode": None,
                "vm_export_strategy": None,
                "historical_source": None,
                "force_live_collection": False,
                "confirm_historical_reconstruction": False,
                "status": "QUEUED",
                "progress": 8,
                "created_at": _utc_now(),
                "started_at": None,
                "ended_at": None,
                "error": None,
                "run_id": run_id,
                "tag_progress": None,
                "export_progress": None,
                "was_export_progress": None,
                "was_recovery": {
                    "run_id": run_id,
                    "checkpoint": str(checkpoint_path),
                    "decision": decision.value,
                },
                "cloud_progress": None,
                "cloud_status": None,
                "warnings": [],
                "vm_export_validation": None,
                "collection_route": None,
                "reconstruction_status": None,
                "collection_sources": [],
                "_checkpoint_path": str(checkpoint_path),
                "_decision": decision.value,
                "_profile_path": str(profile_path),
                "_env_path": str(env_path),
                "_database_env_path": str(database_env_path),
                "_template_path": str(template_path),
                "_assets_dir": str(assets_dir),
            }
            self._jobs[job_id] = job
            self._pending.put(job_id)
            self._trim()
            return {
                key: value for key, value in job.items() if not key.startswith("_")
            }

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            pending = [item for item in self._jobs.values() if item["status"] == "QUEUED"]
            positions = {item["job_id"]: index + 1 for index, item in enumerate(pending)}
            result = []
            for item in sorted(self._jobs.values(), key=lambda row: row["created_at"], reverse=True):
                row = {
                    key: value for key, value in item.items()
                    if not key.startswith("_")
                }
                row["queue_position"] = positions.get(item["job_id"])
                result.append(row)
            return result

    def export_for_cancellation(
        self, job_id: str, export_uuid: str
    ) -> tuple[str, dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError("Trabalho nao encontrado.")
            if job["status"] != "FAILED":
                raise ValueError("O cancelamento exige um trabalho com falha.")
            export = job.get("export_progress")
            if not isinstance(export, Mapping):
                raise ValueError("O trabalho nao possui um export VM travado.")
            if str(export.get("export_uuid") or "") != export_uuid:
                raise ValueError("O UUID informado nao corresponde ao export do trabalho.")
            if str(export.get("status") or "").upper() != "TIMED_OUT":
                raise ValueError("O export VM nao esta marcado como travado.")
            if bool(export.get("auto_cancelled")):
                raise ValueError("O export VM ja foi cancelado automaticamente.")
            return str(job["client_id"]), dict(export)

    def retry(
        self,
        job_id: str,
        *,
        explicit_export_recovery: bool = False,
    ) -> dict[str, Any]:
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
                "vm_selective_mode": original.get("vm_selective_mode"),
                "vm_export_strategy": original.get("vm_export_strategy"),
                "vm_export_uuid": original.get("vm_export_uuid"),
                "historical_source": original.get("historical_source"),
                "was_failure_policy": original.get("was_failure_policy"),
                "confirm_historical_reconstruction": original.get(
                    "confirm_historical_reconstruction", False
                ),
                "force_live_collection": original.get("force_live_collection", False),
            }
            if explicit_export_recovery:
                export = original.get("export_progress")
                timeout_phase = (
                    str(export.get("timeout_phase") or "")
                    if isinstance(export, Mapping) else ""
                )
                failure = ExportTimeoutError(
                    "Recuperacao explicita de export VM.",
                    timeout_phase=timeout_phase or None,
                )
                request["vm_export_strategy"] = recovery_vm_strategy(
                    current_strategy=(
                        str(original.get("vm_export_strategy") or "combined")
                    ),
                    failure=failure,
                    explicit_retry=True,
                )
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
            if job.get("operation") == "cloud_retry":
                command = [
                    sys.executable,
                    "-m",
                    "tenable_reports",
                    "retry-cloud",
                    "--run-id",
                    str(job["run_id"]),
                    "--profile",
                    str(job["_profile_path"]),
                    "--env-file",
                    str(job["_env_path"]),
                    "--database-env-file",
                    str(job["_database_env_path"]),
                    "--cloud-template",
                    str(job["_cloud_template_path"]),
                    "--confirm-live-api",
                ]
            elif job.get("operation") == "staged_remote":
                config = load_orchestration_config(self.config_path)
                client = next(
                    (
                        item
                        for item in config.clients
                        if item.client_id == job["client_id"]
                    ),
                    None,
                )
                if client is None or not client.enabled:
                    raise ValueError("Cliente staged não encontrado ou inativo.")
                run_id = str(job.get("run_id") or f"{job_id}-{job['client_id']}")
                logical_job_id = str(job.get("logical_job_id") or job_id)
                command = [
                    sys.executable,
                    "-m",
                    "tenable_reports",
                    "collect-client",
                    "--mode",
                    str(job["mode"]),
                    "--profile",
                    str(client.profile_path),
                    "--env-file",
                    str(client.env_file),
                    "--database-env-file",
                    str(config.database_env_file),
                    "--output-root",
                    str(config.output_root),
                    "--checkpoint",
                    str(job["_collection_checkpoint_path"]),
                    "--run-id",
                    run_id,
                    "--logical-job-id",
                    logical_job_id,
                    "--attempt-number",
                    str(job.get("attempt_number") or 1),
                    "--origin",
                    (
                        "SCHEDULED"
                        if job["mode"] == "automatic"
                        else "MANUAL"
                    ),
                    "--remote-processing-timeout-seconds",
                    str(config.remote_processing_timeout_seconds),
                    "--remote-progress-warning-seconds",
                    str(config.remote_progress_warning_seconds),
                    "--template",
                    str(config.template_path),
                    "--assets-dir",
                    str(config.assets_dir),
                    "--minimum-free-gb",
                    str(config.minimum_free_gb),
                    "--confirm-live-api",
                ]
                if job.get("_job_control_file"):
                    command.extend((
                        "--job-control-file", str(job["_job_control_file"])
                    ))
                if job.get("force_live_collection"):
                    command.append("--force-live-collection")
                if job.get("vm_selective_mode"):
                    command.extend(("--vm-selective-mode", job["vm_selective_mode"]))
                if job.get("vm_export_strategy"):
                    command.extend(("--vm-export-strategy", job["vm_export_strategy"]))
                if job.get("vm_export_uuid"):
                    command.extend(("--vm-export-uuid", job["vm_export_uuid"]))
                if job.get("historical_source"):
                    command.extend(("--historical-source", job["historical_source"]))
                if job.get("was_failure_policy"):
                    command.extend(("--was-failure-policy", job["was_failure_policy"]))
                if job["days"] is not None:
                    command.extend(("--days", str(job["days"])))
                if job["start_at"]:
                    command.extend(("--start-at", job["start_at"], "--end-at", job["end_at"]))
                for tag in client.tags:
                    command.extend(("--tag", tag))
                if client.include_output:
                    command.append("--include-output")
                if client.include_software_vulns:
                    command.append("--include-software-vulns")
                if client.mask_sensitive:
                    command.append("--mask-sensitive")
            elif job.get("operation") == "staged_build":
                config = load_orchestration_config(self.config_path)
                client = next(
                    (
                        item
                        for item in config.clients
                        if item.client_id == job["client_id"]
                    ),
                    None,
                )
                if client is None:
                    raise ValueError("Cliente staged não encontrado.")
                command = [
                    sys.executable,
                    "-m",
                    "tenable_reports",
                    "build-client",
                    "--profile",
                    str(client.profile_path),
                    "--checkpoint",
                    str(job["_collection_checkpoint_path"]),
                    "--output-root",
                    str(config.output_root),
                    "--database-env-file",
                    str(config.database_env_file),
                    "--template",
                    str(config.template_path),
                    "--assets-dir",
                    str(config.assets_dir),
                ]
                if job.get("_job_control_file"):
                    command.extend((
                        "--job-control-file", str(job["_job_control_file"])
                    ))
                if client.include_output:
                    command.append("--include-output")
                if client.mask_sensitive:
                    command.append("--mask-sensitive")
                if not config.cleanup_after_publish:
                    command.append("--no-cleanup-after-publish")
            elif job.get("operation") in {"was_continue", "was_retry"}:
                command = [
                    sys.executable,
                    "-m",
                    "tenable_reports",
                    "resume-was",
                    "--checkpoint",
                    str(job["_checkpoint_path"]),
                    "--decision",
                    str(job["_decision"]),
                    "--profile",
                    str(job["_profile_path"]),
                    "--env-file",
                    str(job["_env_path"]),
                    "--database-env-file",
                    str(job["_database_env_path"]),
                    "--template",
                    str(job["_template_path"]),
                    "--assets-dir",
                    str(job["_assets_dir"]),
                ]
                if job.get("operation") == "was_retry":
                    command.append("--confirm-live-api")
            else:
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
                if job.get("_job_control_file"):
                    command.extend((
                        "--job-control-file", str(job["_job_control_file"])
                    ))
                if job.get("force_live_collection"):
                    command.append("--force-live-collection")
                if job.get("vm_selective_mode"):
                    command.extend((
                        "--vm-selective-mode", job["vm_selective_mode"]
                    ))
                if job.get("vm_export_strategy"):
                    command.extend((
                        "--vm-export-strategy", job["vm_export_strategy"]
                    ))
                if job.get("vm_export_uuid"):
                    command.extend((
                        "--vm-export-uuid", job["vm_export_uuid"]
                    ))
                if job.get("historical_source"):
                    command.extend((
                        "--historical-source", job["historical_source"]
                    ))
                if job.get("was_failure_policy"):
                    command.extend((
                        "--was-failure-policy", job["was_failure_policy"]
                    ))
                if job["days"] is not None:
                    command.extend(("--days", str(job["days"])))
                if job["start_at"]:
                    command.extend((
                        "--start-at", job["start_at"], "--end-at", job["end_at"]
                    ))
        try:
            def update_progress(event: Mapping[str, Any]) -> None:
                if self.progress_sink is not None:
                    try:
                        self.progress_sink(job_id, event)
                    except Exception:
                        pass
                with self._lock:
                    current_job = self._jobs.get(job_id)
                    if current_job is None:
                        return
                    if event.get("event") == "TENABLE_CLOUD_PROGRESS":
                        current_job["cloud_progress"] = {
                            key: event.get(key)
                            for key in (
                                "status",
                                "stage",
                                "source",
                                "current",
                                "total",
                                "page",
                                "records",
                                "documents",
                                "snapshot_id",
                                "run_id",
                            )
                        }
                        current_job["cloud_status"] = str(
                            event.get("status") or current_job.get("cloud_status") or ""
                        )
                        current_job["progress"] = max(
                            int(current_job.get("progress") or 0), 82
                        )
                        return
                    if event.get("event") == "TENABLE_EXPORT_PROGRESS":
                        source = str(event.get("source") or "")
                        progress_key = (
                            "was_export_progress"
                            if source == "tenable_was_findings"
                            else "export_progress"
                        )
                        current_job[progress_key] = {
                            key: event.get(key)
                            for key in (
                                "source",
                                "export_uuid",
                                "origin",
                                "segment",
                                "date_field",
                                "status",
                                "completed_chunks",
                                "total_chunks",
                                "elapsed_seconds",
                                "processing_elapsed_seconds",
                                "idle_seconds",
                                "last_progress_elapsed_seconds",
                                "no_progress_timeout_seconds",
                                "stalled",
                                "timeout_phase",
                                "filters",
                                "progress_made",
                                "auto_cancelled",
                                "cancellation_error",
                            )
                        }
                        current_job["progress"] = max(
                            int(current_job.get("progress") or 0),
                            45 if progress_key == "was_export_progress" else 35,
                        )
                        return
                    if event.get("event") == "TENABLE_EXPORT_NO_PROGRESS_WARNING":
                        current_job["warnings"] = [
                            *list(current_job.get("warnings") or ()),
                            {
                                "code": "TENABLE_EXPORT_NO_PROGRESS_WARNING",
                                "idle_seconds": event.get("idle_seconds"),
                            },
                        ]
                        return
                    current = int(event.get("current") or 0)
                    total = max(1, int(event.get("total") or 0))
                    current_job["tag_progress"] = {
                        "current": current,
                        "total": total,
                        "tag_uuid": str(event.get("tag_uuid") or ""),
                        "label": str(event.get("tag_label") or ""),
                    }
                    current_job["progress"] = min(
                        92, 45 + round(45 * current / total)
                    )

            control_file = str(job.get("_job_control_file") or "")
            control = FileExecutionControl(control_file) if control_file else None

            def process_started(process_id: int) -> None:
                with self._lock:
                    current_job = self._jobs.get(job_id)
                    if current_job is not None:
                        current_job["process_id"] = process_id
                if self.process_sink is not None:
                    self.process_sink(job_id, process_id)

            def fallback_terminated(process_id: int) -> None:
                with self._lock:
                    current_job = self._jobs.get(job_id)
                    if current_job is not None:
                        current_job["fallback_terminated"] = True
                if self.fallback_sink is not None:
                    self.fallback_sink(job_id, process_id)

            completed = _run_web_command(
                self.runner,
                command,
                self.project_root,
                update_progress,
                cancellation_probe=(
                    control.is_stop_requested if control is not None else None
                ),
                process_started_callback=process_started,
                fallback_callback=fallback_terminated,
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
                job["exit_code"] = completed.returncode
                job["run_id"] = payload.get("run_id") or job.get("run_id")
                client_payloads = [
                    item.get("payload")
                    for item in payload.get("clients") or ()
                    if isinstance(item, Mapping)
                    and isinstance(item.get("payload"), Mapping)
                ]
                result_payloads = client_payloads or ([payload] if payload else [])
                waiting_payload = next(
                    (
                        item for item in result_payloads
                        if str(item.get("status") or "").strip().lower()
                        == "waiting_was_decision"
                    ),
                    None,
                )
                if waiting_payload is not None:
                    job["run_id"] = waiting_payload.get("run_id") or job.get("run_id")
                    job["was_recovery"] = {
                        "run_id": waiting_payload.get("run_id"),
                        "checkpoint": waiting_payload.get("checkpoint"),
                        "failure": dict(waiting_payload.get("was_failure") or {}),
                    }
                job["warnings"] = [
                    dict(warning)
                    for client_payload in result_payloads
                    for warning in (
                        list(client_payload.get("warnings") or ())
                        + list(client_payload.get("cloud_warnings") or ())
                    )
                    if isinstance(warning, Mapping)
                ]
                cloud_payload = next(
                    (
                        item for item in result_payloads
                        if item.get("cloud_status") is not None
                    ),
                    None,
                )
                if cloud_payload is not None:
                    job["cloud_status"] = str(
                        cloud_payload.get("cloud_status") or "UNKNOWN"
                    )
                collection_payload = next(iter(client_payloads), None)
                if collection_payload is not None:
                    job["collection_route"] = collection_payload.get(
                        "collection_route"
                    )
                    job["reconstruction_status"] = collection_payload.get(
                        "reconstruction_status"
                    )
                    job["collection_sources"] = list(
                        collection_payload.get("collection_sources") or ()
                    )
                validation_payload = next(
                    (
                        client_payload for client_payload in client_payloads
                        if client_payload.get("vm_export_mode") == "validation"
                    ),
                    None,
                )
                job["vm_export_validation"] = (
                    {
                        "outcome": str(
                            validation_payload.get("vm_export_outcome") or "UNKNOWN"
                        ),
                        "comparison": validation_payload.get(
                            "vm_export_comparison"
                        ),
                    }
                    if validation_payload is not None else None
                )
                if waiting_payload is not None:
                    job["status"] = "WAITING_WAS_DECISION"
                    job["error"] = None
                elif completed.returncode == 130:
                    job["status"] = "INTERRUPTED"
                    job["error_code"] = "INTERRUPTED_BY_USER"
                    job["error"] = None
                elif completed.returncode == 0:
                    job["status"] = "COMPLETE"
                else:
                    job["status"] = "FAILED"
                    job["error"] = _job_error_from_result(payload, completed)
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
        cloud_connection_checker: Callable[
            [Path, str], dict[str, Any]
        ] = check_cloud_connection,
        tag_lister: Callable[[Path], Sequence[Mapping[str, Any]]] = list_tenable_tags,
        export_canceller: Callable[
            [Path, str], Mapping[str, Any]
        ] = cancel_tenable_export,
        report_registry: ReportRegistry | None = None,
        report_set_purger: ReportSetPurgeService | None = None,
        backfill_state_provider: Callable[[], MainBackfillSourceState] | None = None,
        retention_state_provider: Callable[[], Mapping[str, Any]] | None = None,
        cleanup_status_recorder: Callable[..., Any] | None = None,
        cloud_contract_invalidator: Callable[..., int] | None = None,
        was_recovery_repository: Any | None = None,
        batch_repository: WebBatchRepository | None = None,
        component_repository: ReportComponentRepository | None = None,
        component_retry_enqueuer: Callable[..., Mapping[str, Any]] | None = None,
        require_durable_batches: bool = False,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = DashboardConfigStore(project_root=self.project_root, config_path=config_path)
        self._prepared_archive_lock = threading.RLock()
        self._prepared_archives: dict[
            str, tuple[ReportArchiveResult, float]
        ] = {}
        self._stale_archive_cleanup_timer: threading.Timer | None = None
        self._schedule_stale_archive_cleanup()
        self.connection_checker = connection_checker
        self.cloud_connection_checker = cloud_connection_checker
        self.tag_lister = tag_lister
        self.export_canceller = export_canceller
        self.database_error: str | None = None
        try:
            self.database: DashboardDatabase | None = DashboardDatabase(
                self.config.database_env_path()
            )
        except Exception as exc:
            self.database = None
            self.database_error = _safe_error(str(exc), limit=500)
        self.component_repository = component_repository
        if self.component_repository is None and self.database is not None:
            self.component_repository = PostgresReportComponentRepository(
                self.database.database,
                migrate=False,
            )
        self.component_retry_enqueuer = component_retry_enqueuer
        self.batch_repository = batch_repository
        if self.batch_repository is None and self.database is not None:
            self.batch_repository = PostgresWebBatchRepository(
                self.database.database,
            )
        self.require_durable_batches = bool(require_durable_batches)
        if self.batch_repository is not None:
            eligible_client_count = sum(
                bool(row.get("enabled")) and bool(row.get("credentials_ready"))
                for row in self.config.list_clients()
            )
            if eligible_client_count:
                staged_config = load_orchestration_config(self.config.config_path)
                configured_remote_workers = staged_config.remote_collection_workers
                max_clients_per_batch = staged_config.max_clients_per_batch
                staged_output_root = staged_config.output_root
            else:
                configured_remote_workers = 0
                max_clients_per_batch = 64
                staged_output_root = self.project_root / "data"
            remote_workers = resolve_remote_worker_capacity(
                eligible_client_count=eligible_client_count,
                configured_workers=configured_remote_workers,
                max_clients_per_batch=max_clients_per_batch,
            )
            executor = JobQueue(
                self.project_root,
                self.config.config_path,
                runner,
                start_worker=False,
            )
            self.jobs = DurableDashboardJobQueue(
                repository=self.batch_repository,
                executor=executor,
                worker_id=f"web-{os.getpid()}-{uuid.uuid4().hex[:8]}",
                remote_workers=remote_workers,
                enable_staged_executor=True,
                staged_output_root=staged_output_root,
            )
        else:
            self.jobs = JobQueue(self.project_root, self.config.config_path, runner)
        self.cloud_contract_invalidator = cloud_contract_invalidator
        if self.cloud_contract_invalidator is None and self.database is not None:
            cloud_repository = PostgresCloudSnapshotRepository(
                self.database.database, migrate=False
            )
            self.cloud_contract_invalidator = cloud_repository.invalidate_contract_checks
        self.report_registry = report_registry
        if self.report_registry is None and self.database is not None:
            self.report_registry = PostgresReportRegistry(
                self.database.database, migrate=False
            )
        self.was_recovery_repository = was_recovery_repository
        if self.was_recovery_repository is None and self.database is not None:
            self.was_recovery_repository = PostgresWasRecoveryRepository(
                self.database.database,
                migrate=False,
            )
        self.report_set_purger = report_set_purger
        if (
            self.report_set_purger is None
            and self.database is not None
            and self.report_registry is not None
        ):
            config_payload = self.config.raw()
            defaults = config_payload.get("defaults") or {}
            raw_root = str(defaults.get("output_root") or "../data")
            data_root = (self.config.config_path.parent / raw_root).resolve()
            purge_repository = PostgresReportSetPurgeRepository(
                database=self.database.database,
                registry=self.report_registry,
            )
            self.report_set_purger = ReportSetPurgeService(
                data_root=data_root,
                repository=purge_repository,
                active_jobs=self.jobs.snapshot,
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

    @staticmethod
    def _component_state_payload(
        run_id: str,
        attempts: Sequence[ComponentAttempt],
    ) -> dict[str, Any]:
        latest: dict[ReportComponent, ComponentAttempt] = {}
        for attempt in attempts:
            current = latest.get(attempt.component)
            if current is None or attempt.attempt_number > current.attempt_number:
                latest[attempt.component] = attempt
        ordered_attempts = tuple(
            latest[component]
            for component in ReportComponent
            if component in latest
        )
        summary = summarize_component_set(ordered_attempts)
        return {
            "run_id": run_id,
            "status": summary.status.value,
            "components": [
                {
                    "component": attempt.component.value,
                    "status": attempt.status.value,
                    "stage": attempt.stage.value,
                    "retryable": bool(
                        attempt.retryable
                        and attempt.status
                        in {ComponentStatus.FAILED, ComponentStatus.INTERRUPTED}
                    ),
                }
                for attempt in ordered_attempts
            ],
            "retryable_components": [
                component.value for component in summary.retryable_components
            ],
        }

    def report_component_state(self, run_id: str) -> dict[str, Any]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id não pode ser vazio.")
        if self.report_registry is None:
            raise RuntimeError("Registro de relatórios indisponível.")
        if self.component_repository is None:
            raise RuntimeError("Estados de componentes indisponíveis sem o banco.")
        report = self.report_registry.get_report(normalized_run_id)
        attempts = self.component_repository.latest_attempts(
            source_run_id=normalized_run_id,
            client_id=report.candidate.client_id,
        )
        return self._component_state_payload(normalized_run_id, attempts)

    def retry_report_components(
        self,
        *,
        run_id: str,
        components: Any,
        confirmation: str,
    ) -> Mapping[str, Any]:
        normalized_run_id = str(run_id or "").strip()
        expected = f"RETENTAR COMPONENTES {normalized_run_id}"
        if confirmation.strip() != expected:
            raise ValueError(f'Digite exatamente "{expected}" para confirmar.')
        if self.report_registry is None:
            raise RuntimeError("Registro de relatórios indisponível.")
        report = self.report_registry.get_report(normalized_run_id)
        if report.deleted:
            raise ValueError("Relatórios excluídos não podem ser retentados.")
        state = self.report_component_state(normalized_run_id)
        retryable = tuple(
            ReportComponent(value) for value in state["retryable_components"]
        )
        if components is None:
            selected = retryable
        else:
            if isinstance(components, (str, bytes)) or not isinstance(
                components,
                Sequence,
            ):
                raise ValueError("components deve ser uma lista.")
            try:
                requested = {ReportComponent(value) for value in components}
            except (TypeError, ValueError) as exc:
                raise ValueError("Componente desconhecido.") from exc
            selected = tuple(
                component for component in ReportComponent if component in requested
            )
        if not selected:
            raise ValueError("Nenhum componente retentável foi selecionado.")
        if any(component not in retryable for component in selected):
            raise ValueError("COMPONENT_NOT_RETRYABLE")
        if self.component_retry_enqueuer is not None:
            return self.component_retry_enqueuer(
                run_id=normalized_run_id,
                client_id=report.candidate.client_id,
                selected_components=selected,
                failed_only=True,
            )
        if selected == (ReportComponent.CLOUD,):
            return self.retry_cloud_report(
                run_id=normalized_run_id,
                confirmation=f"RETENTAR CLOUD {normalized_run_id}",
            )
        raise RuntimeError(
            "O executor de retentativas seletivas ainda não está disponível."
        )

    def report_rows(
        self, client_id: str, *, include_deleted: bool = False
    ) -> list[dict[str, Any]]:
        if self.report_registry is None:
            return []
        documents_by_run: dict[str, list[dict[str, Any]]] = {}
        cloud_results: dict[str, dict[str, Any]] = {}
        if self.database is not None:
            for document in self.database.reports(client_id):
                documents_by_run.setdefault(str(document.get("run_id") or ""), []).append(document)
            cloud_results = self.database.cloud_results(client_id)
        rows = []
        for report in self.report_registry.list_reports(
            client_id=client_id, include_deleted=include_deleted
        ):
            candidate = report.candidate
            key = reference_key_for_candidate(candidate)
            main = self.report_registry.get_main(key)
            documents = documents_by_run.get(candidate.run_id, [])
            cloud_documents = [
                item for item in documents
                if str(item.get("document_kind") or "") == "cloud"
            ]
            cloud_result = cloud_results.get(candidate.run_id, {})
            cloud_status = (
                "COMPLETE" if cloud_documents
                else str(cloud_result.get("status") or "NOT_REQUESTED")
            )
            cloud_warnings = [
                dict(item) for item in cloud_result.get("warnings") or ()
                if isinstance(item, Mapping)
            ]
            cloud_retry_available = (
                not report.deleted_at
                and not cloud_documents
                and cloud_status == "FAILED"
                and any(bool(item.get("retryable")) for item in cloud_warnings)
            )
            component_state: dict[str, Any] | None = None
            if self.component_repository is not None and not report.deleted_at:
                component_attempts = self.component_repository.latest_attempts(
                    source_run_id=candidate.run_id,
                    client_id=candidate.client_id,
                )
                if component_attempts:
                    component_state = self._component_state_payload(
                        candidate.run_id,
                        component_attempts,
                    )
            row = {
                "run_id": candidate.run_id,
                "period_id": key.period_key,
                "period_start_at": candidate.period_start_at,
                "period_end_at": candidate.period_end_at,
                "origin": candidate.origin.value,
                "execution_type": candidate.execution_type,
                "status": candidate.publication_status,
                "is_main": bool(main and main.run_id == candidate.run_id),
                "main_set_at": (
                    main.set_at if main and main.run_id == candidate.run_id else None
                ),
                "deleted_at": report.deleted_at,
                "reference_run_id": None,
                "size_bytes": sum(int(item.get("size_bytes") or 0) for item in documents),
                "omitted_modules": [],
                "cloud_status": cloud_status,
                "cloud_warnings": cloud_warnings,
                "cloud_retry_available": cloud_retry_available,
                "documents": documents,
            }
            if component_state is not None:
                row["component_state"] = component_state
            rows.append(row)
        return rows

    def _archive_roots(self) -> tuple[Path, Path]:
        payload = self.config.raw()
        raw_root = str((payload.get("defaults") or {}).get("output_root") or "../data")
        data_root = (self.config.config_path.parent / raw_root).resolve()
        return data_root, data_root / ".downloads"

    def _schedule_stale_archive_cleanup(self) -> None:
        _, temporary_root = self._archive_roots()
        if not temporary_root.is_dir():
            return
        now = time.time()
        remaining_delays: list[float] = []
        for path in temporary_root.glob("tenable-reports-*.zip"):
            try:
                age_seconds = max(0.0, now - path.stat().st_mtime)
                if age_seconds >= PREPARED_ARCHIVE_TTL_SECONDS:
                    path.unlink(missing_ok=True)
                else:
                    remaining_delays.append(
                        PREPARED_ARCHIVE_TTL_SECONDS - age_seconds + 1.0
                    )
            except OSError:
                continue
        if remaining_delays:
            timer = threading.Timer(
                min(remaining_delays),
                self._schedule_stale_archive_cleanup,
            )
            timer.daemon = True
            self._stale_archive_cleanup_timer = timer
            timer.start()

    @staticmethod
    def _archive_report_from_row(
        row: Mapping[str, Any], *, display_name: str
    ) -> ArchiveReportSet:
        return ArchiveReportSet(
            client_id=str(row.get("client_id") or ""),
            display_name=display_name,
            run_id=str(row.get("run_id") or ""),
            period_id=str(row.get("period_id") or ""),
            is_main=bool(row.get("is_main")),
            deleted=bool(row.get("deleted_at")),
            main_set_at=str(row.get("main_set_at") or "") or None,
            documents=tuple(
                ArchiveDocument(
                    path=Path(str(document.get("path") or "")),
                    name=str(document.get("name") or "") or None,
                )
                for document in row.get("documents") or ()
                if isinstance(document, Mapping) and document.get("path")
            ),
        )

    def report_archive_months(self) -> list[str]:
        periods: set[str] = set()
        for client in self.config.list_clients():
            for row in self.report_rows(str(client["client_id"])):
                period_id = str(row.get("period_id") or "")
                if bool(row.get("is_main")) and re.fullmatch(r"\d{4}-\d{2}", period_id):
                    periods.add(period_id)
        return sorted(periods, reverse=True)

    def create_report_set_archive(self, run_id: str) -> ReportArchiveResult:
        if self.report_registry is None or self.database is None:
            raise RuntimeError("Banco e registro de relatórios precisam estar disponíveis.")
        registered = self.report_registry.get_report(run_id)
        if registered.deleted:
            raise KeyError("Conjunto de relatórios excluído.")
        client_id = registered.candidate.client_id
        client = next(
            (
                item for item in self.config.list_clients()
                if str(item.get("client_id") or "") == client_id
            ),
            {"display_name": client_id},
        )
        row = next(
            (
                item for item in self.report_rows(client_id, include_deleted=True)
                if str(item.get("run_id") or "") == run_id
            ),
            None,
        )
        if row is None or row.get("deleted_at"):
            raise KeyError("Conjunto de relatórios não encontrado.")
        row = {
            **row,
            "client_id": client_id,
            "documents": self.database.report_documents(run_id),
        }
        data_root, temporary_root = self._archive_roots()
        return build_report_set_archive(
            data_root=data_root,
            temporary_root=temporary_root,
            report=self._archive_report_from_row(
                row,
                display_name=str(client.get("display_name") or client_id),
            ),
        )

    def create_monthly_report_archive(self, period_id: str) -> ReportArchiveResult:
        if self.report_registry is None or self.database is None:
            raise RuntimeError("Banco e registro de relatórios precisam estar disponíveis.")
        clients: list[ArchiveClient] = []
        for client in self.config.list_clients():
            client_id = str(client.get("client_id") or "")
            display_name = str(client.get("display_name") or client_id)
            reports = tuple(
                self._archive_report_from_row(
                    {
                        **row,
                        "client_id": client_id,
                        "documents": self.database.report_documents(
                            str(row.get("run_id") or "")
                        ),
                    },
                    display_name=display_name,
                )
                for row in self.report_rows(client_id)
                if (
                    str(row.get("period_id") or "") == period_id
                    and bool(row.get("is_main"))
                    and not row.get("deleted_at")
                )
            )
            clients.append(ArchiveClient(
                client_id=client_id,
                display_name=display_name,
                reports=reports,
            ))
        data_root, temporary_root = self._archive_roots()
        return build_monthly_report_archive(
            data_root=data_root,
            temporary_root=temporary_root,
            period_id=period_id,
            clients=clients,
        )

    def prepare_report_archive(
        self,
        *,
        run_id: str | None = None,
        period_id: str | None = None,
    ) -> dict[str, str]:
        normalized_run = str(run_id or "").strip()
        normalized_period = str(period_id or "").strip()
        if bool(normalized_run) == bool(normalized_period):
            raise ValueError("Informe run_id ou period_id, mas não ambos.")
        archive = (
            self.create_report_set_archive(normalized_run)
            if normalized_run
            else self.create_monthly_report_archive(normalized_period)
        )
        download_id = uuid.uuid4().hex
        expires_at = time.monotonic() + PREPARED_ARCHIVE_TTL_SECONDS
        with self._prepared_archive_lock:
            self._prepared_archives[download_id] = (archive, expires_at)
        timer = threading.Timer(
            PREPARED_ARCHIVE_TTL_SECONDS,
            self._discard_prepared_archive,
            args=(download_id,),
        )
        timer.daemon = True
        try:
            timer.start()
        except Exception:
            with self._prepared_archive_lock:
                self._prepared_archives.pop(download_id, None)
            archive.path.unlink(missing_ok=True)
            raise
        return {
            "download_id": download_id,
            "download_name": archive.download_name,
            "download_url": f"/api/report-archives/download/{download_id}",
        }

    def claim_prepared_archive(self, download_id: str) -> ReportArchiveResult:
        if not re.fullmatch(r"[a-f0-9]{32}", download_id):
            raise KeyError("Download preparado não encontrado.")
        with self._prepared_archive_lock:
            prepared = self._prepared_archives.pop(download_id, None)
        if prepared is None:
            raise KeyError("Download preparado não encontrado ou expirado.")
        archive, expires_at = prepared
        if time.monotonic() >= expires_at:
            archive.path.unlink(missing_ok=True)
            raise KeyError("Download preparado não encontrado ou expirado.")
        return archive

    def _discard_prepared_archive(self, download_id: str) -> None:
        with self._prepared_archive_lock:
            prepared = self._prepared_archives.pop(download_id, None)
        if prepared is not None:
            archive, _ = prepared
            archive.path.unlink(missing_ok=True)

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

    def enqueue_jobs(
        self,
        client_ids: Sequence[str],
        request: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        run_scope = str(request.get("run_scope") or "single").strip().lower()
        if run_scope not in {"single", "all"}:
            raise ValueError("Escopo de execucao invalido.")
        client_rows = self.config.list_clients()
        clients = {item["client_id"]: item for item in client_rows}
        batch_options: dict[str, Any] = {"execution_model": "STAGED_V1"}
        if run_scope == "all":
            batch_options.update(build_manual_batch_options(
                clients=client_rows,
                selected_client_ids=client_ids,
                selection_filter_snapshot=(
                    request.get("selection_filter_snapshot")
                    or {
                        "analyst_id": None,
                        "query": "",
                        "unassigned": False,
                    }
                ),
            ))
        exact_period = bool(
            (request.get("start_at") and request.get("end_at"))
            or (request.get("start_date") and request.get("end_date"))
        )
        historical_clients = [
            client_id
            for client_id in client_ids
            if str(clients.get(client_id, {}).get("historical_source") or "legacy")
            == "inventory_beta"
        ]
        if (
            exact_period
            and historical_clients
            and not bool(request.get("confirm_historical_reconstruction", False))
        ):
            raise ValueError(
                "O periodo historico pode exigir reconstrucao pela Inventory API "
                "quando nao houver snapshot. Confirme a reconstrucao para continuar."
            )

        requests: list[tuple[str, Mapping[str, Any]]] = []
        for client_id in client_ids:
            client = clients.get(client_id)
            if client is None:
                raise ValueError(f"Cliente nao encontrado: {client_id}")
            client_request = dict(request)
            client_request.pop("client_ids", None)
            client_request.pop("selection_filter_snapshot", None)
            if run_scope == "all":
                client_request["was_failure_policy"] = "retry_then_continue"
            client_request["historical_source"] = str(
                client.get("historical_source") or "legacy"
            ).replace("_", "-")
            client_request["vm_export_strategy"] = str(
                client.get("vm_export_strategy") or "combined"
            )
            requests.append((client_id, client_request))
        if self.require_durable_batches and self.batch_repository is None:
            raise RuntimeError(
                "Banco PostgreSQL indisponivel; novas geracoes estao bloqueadas."
            )
        enqueue_requests = getattr(self.jobs, "enqueue_requests", None)
        if callable(enqueue_requests):
            return enqueue_requests(
                tuple(requests),
                batch_options=batch_options,
            )
        created: list[dict[str, Any]] = []
        for client_id, client_request in requests:
            created.extend(self.jobs.enqueue([client_id], client_request))
        return created

    def batch_list(self) -> dict[str, Any]:
        snapshot = getattr(self.jobs, "batches_snapshot", None)
        if not callable(snapshot):
            raise RuntimeError("Controle duravel de lotes indisponivel.")
        return {"batches": snapshot()}

    def batch_state(self, batch_id: str) -> dict[str, Any]:
        snapshot = getattr(self.jobs, "batch_snapshot", None)
        if not callable(snapshot):
            raise RuntimeError("Controle duravel de lotes indisponivel.")
        return snapshot(batch_id)

    def request_batch_action(
        self,
        batch_id: str,
        action: BatchAction,
        *,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_action = getattr(self.jobs, "request_action", None)
        if not callable(request_action):
            raise RuntimeError("Controle duravel de lotes indisponivel.")
        request_action(batch_id, action)
        return self.batch_state(batch_id)

    def derive_batch(
        self,
        request: DerivedBatchRequest,
    ) -> dict[str, Any]:
        derive = getattr(self.jobs, "derive_batch", None)
        if not callable(derive):
            raise RuntimeError("Derivacao duravel de lotes indisponivel.")
        return derive(request)

    def cancel_export_and_retry(
        self,
        *,
        job_id: str,
        export_uuid: str,
        confirmation: str,
    ) -> dict[str, Any]:
        normalized_uuid = export_uuid.strip()
        expected = f"CANCELAR {normalized_uuid}"
        if confirmation.strip() != expected:
            raise ValueError(f'Digite exatamente "{expected}" para confirmar.')
        client_id, export = self.jobs.export_for_cancellation(
            job_id, normalized_uuid
        )
        cancelled = dict(self.export_canceller(
            self.config.client_env_path(client_id), normalized_uuid
        ))
        retried = self.jobs.retry(job_id, explicit_export_recovery=True)
        return {
            "cancelled_export": {**export, **cancelled},
            "job": retried,
        }

    def update_client(
        self, client_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        clients = {item["client_id"]: item for item in self.config.list_clients()}
        before = clients.get(client_id)
        if before is None:
            raise KeyError("Cliente nao encontrado.")
        updated = self.config.update_client(client_id, values)
        token_changed = bool(str(values.get("cloud_api_secret") or "").strip())
        environment_changed = (
            "cloud_environment" in values
            and str(values.get("cloud_environment") or "").strip().lower()
            != str(before.get("cloud_environment") or "global")
        )
        if (
            self.cloud_contract_invalidator is not None
            and (token_changed or environment_changed)
        ):
            environments = {
                str(before.get("cloud_environment") or "global"),
                str(updated.get("cloud_environment") or "global"),
            }
            try:
                invalidated = sum(
                    int(self.cloud_contract_invalidator(
                        client_id=client_id,
                        environment=environment,
                    ))
                    for environment in environments
                )
                updated["cloud_contract_cache_invalidated"] = invalidated
            except Exception:
                updated["cloud_contract_cache_invalidated"] = None
        return updated

    def check_cloud_client(self, client_id: str) -> dict[str, Any]:
        clients = {item["client_id"]: item for item in self.config.list_clients()}
        client = clients.get(client_id)
        if client is None:
            raise KeyError("Cliente nao encontrado.")
        if not client.get("cloud_enabled"):
            raise ValueError("Cloud Security nao esta habilitado neste cliente.")
        return {
            "client_id": client_id,
            "display_name": client["display_name"],
            **self.cloud_connection_checker(
                self.config.client_env_path(client_id),
                str(client.get("cloud_environment") or "global"),
            ),
        }

    def check_connections(self, client_ids: Sequence[str]) -> list[dict[str, Any]]:
        clients = {item["client_id"]: item for item in self.config.list_clients()}
        unknown = [client_id for client_id in client_ids if client_id not in clients]
        if unknown:
            raise ValueError("Clientes nao encontrados: " + ", ".join(unknown))
        if not client_ids:
            raise ValueError("Nenhum cliente foi selecionado para o teste.")

        def check_client(client_id: str) -> dict[str, Any]:
            client = clients[client_id]
            try:
                vm_result = self.connection_checker(
                    self.config.client_env_path(client_id)
                )
            except Exception as exc:
                vm_result = {
                    "ok": False,
                    "latency_ms": 0,
                    "message": _safe_error(str(exc), limit=300),
                    "checked_at": _utc_now(),
                }
            result = {
                "client_id": client_id,
                "display_name": client["display_name"],
                **vm_result,
            }
            if client.get("cloud_enabled"):
                try:
                    result["cloud"] = self.cloud_connection_checker(
                        self.config.client_env_path(client_id),
                        str(client.get("cloud_environment") or "global"),
                    )
                except Exception as exc:
                    result["cloud"] = {
                        "ok": False,
                        "latency_ms": 0,
                        "message": _safe_error(str(exc), limit=300),
                        "checked_at": _utc_now(),
                        "retryable": bool(getattr(exc, "retryable", False)),
                    }
            else:
                result["cloud"] = None
            return result

        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(4, len(client_ids))) as executor:
            future_map = {
                executor.submit(check_client, client_id): client_id
                for client_id in client_ids
            }
            for future in as_completed(future_map):
                client_id = future_map[future]
                results[client_id] = future.result()
        return [results[client_id] for client_id in client_ids]

    def retry_cloud_report(
        self, *, run_id: str, confirmation: str
    ) -> dict[str, Any]:
        expected = f"RETENTAR CLOUD {run_id}"
        if confirmation.strip() != expected:
            raise ValueError(f'Digite exatamente "{expected}" para confirmar.')
        if self.report_registry is None:
            raise RuntimeError("Registro de relatorios indisponivel.")
        report = self.report_registry.get_report(run_id)
        client_id = report.candidate.client_id
        clients = {item["client_id"]: item for item in self.config.list_clients()}
        client = clients.get(client_id)
        if client is None:
            raise KeyError("Cliente nao encontrado.")
        if not client.get("enabled"):
            raise ValueError("O cliente esta desabilitado.")
        if not client.get("cloud_enabled"):
            raise ValueError("Cloud Security nao esta habilitado neste cliente.")
        if not client.get("cloud_token_saved"):
            raise ValueError("O cliente nao possui token Cloud Security salvo.")
        row = next(
            (
                item for item in self.report_rows(client_id, include_deleted=True)
                if item.get("run_id") == run_id
            ),
            None,
        )
        if row is None or not row.get("cloud_retry_available"):
            raise ValueError("Esta execucao nao possui uma falha Cloud retentavel.")
        defaults = self.config.raw().get("defaults") or {}
        raw_template = str(
            defaults.get("cloud_template")
            or "../templates/corporate/cloud-base-v1.docx"
        )
        cloud_template = (
            self.config.config_path.parent / raw_template
        ).resolve()
        return self.jobs.enqueue_cloud_retry(
            run_id=run_id,
            client_id=client_id,
            profile_path=self.config.client_profile_path(client_id),
            env_path=self.config.client_env_path(client_id),
            database_env_path=self.config.database_env_path(),
            cloud_template_path=cloud_template,
        )

    def recover_was_report(
        self,
        *,
        run_id: str,
        action: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if self.was_recovery_repository is None:
            raise RuntimeError("Recuperacao WAS indisponivel sem o PostgreSQL.")
        decisions = {
            "continue": WasRecoveryDecision.CONTINUE_WITHOUT_WAS,
            "retry": WasRecoveryDecision.RETRY_WAS,
        }
        decision = decisions.get(action)
        if decision is None:
            raise ValueError("Acao de recuperacao WAS invalida.")
        expected = (
            f"RETENTAR WAS {run_id}"
            if decision is WasRecoveryDecision.RETRY_WAS
            else f"CONTINUAR SEM WAS {run_id}"
        )
        if confirmation.strip() != expected:
            raise ValueError(f'Digite exatamente "{expected}" para confirmar.')
        record = self.was_recovery_repository.get(run_id)
        if record is None:
            raise KeyError("Recuperacao WAS nao encontrada.")
        if record.status not in {
            WasRecoveryStatus.WAITING_WAS_DECISION,
            WasRecoveryStatus.RETRY_AVAILABLE,
        }:
            raise ValueError("Esta recuperacao WAS nao aceita uma nova decisao.")
        if (
            record.status is WasRecoveryStatus.RETRY_AVAILABLE
            and decision is not WasRecoveryDecision.RETRY_WAS
        ):
            raise ValueError(
                "Uma publicacao automatica concluida aceita somente a retentativa WEB."
            )
        clients = {item["client_id"]: item for item in self.config.list_clients()}
        client = clients.get(record.client_id)
        if client is None:
            raise KeyError("Cliente nao encontrado.")
        if not client.get("enabled"):
            raise ValueError("O cliente esta desabilitado.")
        if decision is WasRecoveryDecision.RETRY_WAS:
            if not client.get("was_enabled"):
                raise ValueError("Vulnerabilidades WEB nao estao habilitadas.")
            if not client.get("credentials_ready"):
                raise ValueError("O cliente nao possui credenciais Tenable prontas.")
        checkpoint_path = Path(record.checkpoint_path).resolve()
        if not checkpoint_path.is_file():
            raise ValueError("Checkpoint WAS nao encontrado no disco.")
        defaults = self.config.raw().get("defaults") or {}
        config_directory = self.config.config_path.parent
        template_path = (
            config_directory
            / str(defaults.get("template") or "../templates/corporate/base-v1.docx")
        ).resolve()
        assets_dir = (
            config_directory
            / str(defaults.get("assets_dir") or "../templates/corporate/assets")
        ).resolve()
        return self.jobs.enqueue_was_recovery(
            run_id=record.run_id,
            client_id=record.client_id,
            decision=decision,
            checkpoint_path=checkpoint_path,
            profile_path=self.config.client_profile_path(record.client_id),
            env_path=self.config.client_env_path(record.client_id),
            database_env_path=self.config.database_env_path(),
            template_path=template_path,
            assets_dir=assets_dir,
        )

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
        was_recoveries: list[dict[str, Any]] = []
        for client in clients:
            client_id = client["client_id"]
            client_recoveries: list[dict[str, Any]] = []
            if self.was_recovery_repository is not None:
                try:
                    for record in self.was_recovery_repository.pending(
                        client_id=client_id
                    ):
                        failure = record.checkpoint.was_failure
                        item = {
                            "run_id": record.run_id,
                            "client_id": record.client_id,
                            "status": record.status.value,
                            "checkpoint": record.checkpoint_path,
                            "failure": failure.to_dict() if failure else None,
                            "updated_at": record.updated_at,
                        }
                        client_recoveries.append(item)
                        was_recoveries.append(item)
                except Exception as exc:
                    database_error = _safe_error(str(exc), limit=500)
            client["was_recoveries"] = client_recoveries
            client["latest_report"] = summaries.get(client_id)
            client["job"] = latest_job.get(client_id)
            client["alert"] = latest_alert.get(client_id)
        batches_snapshot = getattr(self.jobs, "batches_snapshot", None)
        batches = batches_snapshot() if callable(batches_snapshot) else []
        return {
            "clients": clients,
            "analysts": self.config.list_analysts(),
            "jobs": jobs,
            "batches": batches,
            "alerts": alerts,
            "was_recoveries": was_recoveries,
            "database_error": database_error,
            "server_time": _utc_now(),
            "queue_mode": (
                "staged_v1" if self.batch_repository is not None else "legacy"
            ),
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
            if name not in {"app.css", "app.js", "client_selection.js"}:
                self._json_error(HTTPStatus.NOT_FOUND, "Arquivo nao encontrado.")
                return
            self._send_static(name, mimetypes.guess_type(name)[0] or "text/plain")
            return
        if parsed.path == "/api/state":
            try:
                self._json(HTTPStatus.OK, self.app.state())
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                self._json_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Falha ao carregar a configuração local.",
                )
            return
        if parsed.path == "/api/analysts":
            try:
                self._json(
                    HTTPStatus.OK,
                    {"analysts": self.app.config.list_analysts()},
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                self._json_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Falha ao carregar a configuração local.",
                )
            return
        archive_builder: Callable[[], ReportArchiveResult] | None = None
        if parsed.path == "/api/report-archives/months":
            try:
                self._json(HTTPStatus.OK, {
                    "periods": self.app.report_archive_months(),
                })
            except RuntimeError as exc:
                self._json_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    _safe_error(str(exc), limit=500),
                )
            except Exception as exc:
                self._json_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _safe_error(str(exc), limit=500),
                )
            return
        prepared_match = re.fullmatch(
            r"/api/report-archives/download/([a-f0-9]{32})",
            parsed.path,
        )
        if prepared_match:
            archive_builder = lambda: self.app.claim_prepared_archive(
                prepared_match.group(1)
            )
        if archive_builder is not None:
            try:
                self._download_archive(archive_builder())
            except KeyError as exc:
                self._json_error(HTTPStatus.NOT_FOUND, _safe_error(str(exc), limit=500))
            except ValueError as exc:
                self._json_error(HTTPStatus.BAD_REQUEST, _safe_error(str(exc), limit=500))
            except EmptyReportArchiveError as exc:
                self._json_error(HTTPStatus.CONFLICT, _safe_error(str(exc), limit=500))
            except UnsafeReportArchivePath as exc:
                self._json_error(HTTPStatus.CONFLICT, _safe_error(str(exc), limit=500))
            except InsufficientReportArchiveSpace as exc:
                self._json_error(
                    HTTPStatus.INSUFFICIENT_STORAGE,
                    _safe_error(str(exc), limit=500),
                )
            except RuntimeError as exc:
                self._json_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    _safe_error(str(exc), limit=500),
                )
            except Exception as exc:
                self.log_message(
                    "Falha inesperada ao gerar ZIP: %s",
                    type(exc).__name__,
                )
                self._json_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Não foi possível gerar o arquivo ZIP.",
                )
            return
        if parsed.path == "/api/batches":
            try:
                self._json(HTTPStatus.OK, self.app.batch_list())
            except RuntimeError as exc:
                self._json_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    _safe_error(str(exc), limit=300),
                )
            return
        match = re.fullmatch(r"/api/batches/([^/]+)", parsed.path)
        if match:
            try:
                self._json(
                    HTTPStatus.OK,
                    self.app.batch_state(unquote(match.group(1))),
                )
            except KeyError as exc:
                self._json_error(
                    HTTPStatus.NOT_FOUND,
                    _safe_error(str(exc), limit=300),
                )
            except (ValueError, RuntimeError) as exc:
                self._json_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    _safe_error(str(exc), limit=300),
                )
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
        match = re.fullmatch(r"/api/reports/([^/]+)/components", parsed.path)
        if match:
            try:
                self._json(
                    HTTPStatus.OK,
                    self.app.report_component_state(unquote(match.group(1))),
                )
            except KeyError as exc:
                self._json_error(
                    HTTPStatus.NOT_FOUND,
                    _safe_error(str(exc), limit=300),
                )
            except ValueError as exc:
                self._json_error(
                    HTTPStatus.BAD_REQUEST,
                    _safe_error(str(exc), limit=300),
                )
            except RuntimeError as exc:
                self._json_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    _safe_error(str(exc), limit=300),
                )
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
        match = re.fullmatch(r"/api/reports/([^/]+)/purge-preview", parsed.path)
        if match:
            if self.app.report_set_purger is None:
                self._json_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "Exclusão permanente indisponível sem o banco configurado.",
                )
                return
            try:
                preview = self.app.report_set_purger.preview(
                    unquote(match.group(1))
                )
                self._json(HTTPStatus.OK, preview.to_dict())
            except KeyError as exc:
                self._json_error(HTTPStatus.NOT_FOUND, _safe_error(str(exc), limit=500))
            except UnsafeReportSetPath as exc:
                self._json_error(HTTPStatus.CONFLICT, _safe_error(str(exc), limit=500))
            except Exception as exc:
                self._json_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _safe_error(str(exc), limit=500),
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
            match = re.fullmatch(
                r"/api/batches/([^/]+)/(retry-incomplete|rerun-all)",
                parsed.path,
            )
            if match:
                action = (
                    BatchAction.RETRY_INCOMPLETE
                    if match.group(2) == "retry-incomplete"
                    else BatchAction.RERUN_ALL
                )
                result = self.app.derive_batch(
                    DerivedBatchRequest(
                        source_batch_id=uuid.UUID(unquote(match.group(1))),
                        kind=action,
                        idempotency_key=str(
                            payload.get("idempotency_key") or ""
                        ),
                        confirmation_token=(
                            str(payload.get("confirmation") or "") or None
                        ),
                        actor=str(payload.get("actor") or "") or None,
                        reason=str(payload.get("reason") or "") or None,
                    )
                )
                self._json(HTTPStatus.ACCEPTED, result)
                return
            match = re.fullmatch(
                r"/api/batches/([^/]+)/(pause|resume|stop)",
                parsed.path,
            )
            if match:
                batch_id = unquote(match.group(1))
                action = BatchAction(match.group(2).upper())
                if action is BatchAction.STOP:
                    expected = f"PARAR {batch_id[:8]}"
                    if str(payload.get("confirmation") or "").strip() != expected:
                        raise ValueError(
                            f'Digite exatamente "{expected}" para confirmar.'
                        )
                idempotency_key = str(
                    payload.get("idempotency_key") or ""
                ).strip()
                if not idempotency_key:
                    raise ValueError(
                        "A chave idempotente da acao de lote e obrigatoria."
                    )
                result = self.app.request_batch_action(
                    batch_id,
                    action,
                    actor=str(payload.get("actor") or "")[:200],
                    reason=str(payload.get("reason") or "")[:500],
                    idempotency_key=idempotency_key[:200],
                )
                self._json(HTTPStatus.OK, result)
                return
            if parsed.path == "/api/report-archives/prepare":
                try:
                    result = self.app.prepare_report_archive(
                        run_id=str(payload.get("run_id") or "") or None,
                        period_id=str(payload.get("period_id") or "") or None,
                    )
                    self._json(HTTPStatus.CREATED, result)
                except KeyError as exc:
                    self._json_error(
                        HTTPStatus.NOT_FOUND,
                        _safe_error(str(exc), limit=500),
                    )
                except (EmptyReportArchiveError, UnsafeReportArchivePath) as exc:
                    self._json_error(
                        HTTPStatus.CONFLICT,
                        _safe_error(str(exc), limit=500),
                    )
                except InsufficientReportArchiveSpace as exc:
                    self._json_error(
                        HTTPStatus.INSUFFICIENT_STORAGE,
                        _safe_error(str(exc), limit=500),
                    )
                except ValueError as exc:
                    self._json_error(
                        HTTPStatus.BAD_REQUEST,
                        _safe_error(str(exc), limit=500),
                    )
                except RuntimeError as exc:
                    self._json_error(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        _safe_error(str(exc), limit=500),
                    )
                except Exception as exc:
                    self.log_message(
                        "Falha inesperada ao preparar ZIP: %s",
                        type(exc).__name__,
                    )
                    self._json_error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "Não foi possível preparar o arquivo ZIP.",
                    )
                return
            if parsed.path == "/api/analysts":
                analyst = self.app.config.create_analyst(payload)
                self._json(HTTPStatus.CREATED, {"analyst": analyst})
                return
            if parsed.path == "/api/clients":
                client = self.app.config.add_client(payload)
                self._json(HTTPStatus.CREATED, {"client": client})
                return
            match = re.fullmatch(r"/api/clients/([^/]+)/cloud/check", parsed.path)
            if match:
                result = self.app.check_cloud_client(unquote(match.group(1)))
                self._json(HTTPStatus.OK, {"result": result})
                return
            match = re.fullmatch(
                r"/api/clients/([^/]+)/vm-export/validate", parsed.path
            )
            if match:
                client_id = unquote(match.group(1))
                client = next(
                    (
                        item for item in self.app.config.list_clients()
                        if item["client_id"] == client_id
                    ),
                    None,
                )
                if client is None:
                    raise KeyError("Cliente nao encontrado.")
                if not client["enabled"]:
                    raise ValueError("O cliente esta desabilitado.")
                if not client["credentials_ready"]:
                    raise ValueError("O cliente nao possui credenciais prontas.")
                jobs = self.app.jobs.enqueue([client_id], {
                    "mode": "manual",
                    "vm_selective_mode": "validation",
                })
                if not jobs:
                    raise ValueError("O cliente ja esta na fila ou em execucao.")
                self._json(HTTPStatus.ACCEPTED, {"job": jobs[0]})
                return
            if parsed.path == "/api/jobs":
                known = {
                    item["client_id"] for item in self.app.config.list_clients() if item["enabled"]
                }
                requested = (
                    payload["client_ids"]
                    if "client_ids" in payload
                    else sorted(known)
                )
                if not isinstance(requested, list) or any(
                    not isinstance(item, str) or item not in known
                    for item in requested
                ):
                    raise ValueError("Ha clientes inexistentes ou desabilitados na selecao.")
                jobs = self.app.enqueue_jobs(requested, payload)
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
            match = re.fullmatch(r"/api/reports/([^/]+)/retry-cloud", parsed.path)
            if match:
                job = self.app.retry_cloud_report(
                    run_id=unquote(match.group(1)),
                    confirmation=str(payload.get("confirmation") or ""),
                )
                self._json(HTTPStatus.ACCEPTED, {"job": job})
                return
            match = re.fullmatch(
                r"/api/reports/([^/]+)/retry-components",
                parsed.path,
            )
            if match:
                job = self.app.retry_report_components(
                    run_id=unquote(match.group(1)),
                    components=payload.get("components"),
                    confirmation=str(payload.get("confirmation") or ""),
                )
                self._json(HTTPStatus.ACCEPTED, {"job": job})
                return
            match = re.fullmatch(
                r"/api/was-recoveries/([^/]+)/(continue|retry)",
                parsed.path,
            )
            if match:
                job = self.app.recover_was_report(
                    run_id=unquote(match.group(1)),
                    action=match.group(2),
                    confirmation=str(payload.get("confirmation") or ""),
                )
                self._json(HTTPStatus.ACCEPTED, {"job": job})
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
            match = re.fullmatch(
                r"/api/jobs/([^/]+)/cancel-export-and-retry", parsed.path
            )
            if match:
                result = self.app.cancel_export_and_retry(
                    job_id=unquote(match.group(1)),
                    export_uuid=str(payload.get("export_uuid") or ""),
                    confirmation=str(payload.get("confirmation") or ""),
                )
                self._json(HTTPStatus.ACCEPTED, result)
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
        except BatchClientConflictError as exc:
            self._json_error(
                HTTPStatus.CONFLICT,
                _safe_error(str(exc), limit=500),
            )
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
        analyst_match = re.fullmatch(r"/api/analysts/([^/]+)", parsed.path)
        if analyst_match:
            try:
                payload = self._request_json()
                if payload.get("confirmation") != "EXCLUIR":
                    raise ValueError('Digite exatamente "EXCLUIR" para confirmar.')
                analyst_id = unquote(analyst_match.group(1))
                self.app.config.delete_analyst(analyst_id)
                self._json(
                    HTTPStatus.OK,
                    {"deleted_analyst_id": analyst_id},
                )
            except AnalystInUseError as exc:
                self._json_error(
                    HTTPStatus.CONFLICT,
                    _safe_error(str(exc), limit=500),
                )
            except (ValueError, json.JSONDecodeError) as exc:
                self._json_error(
                    HTTPStatus.BAD_REQUEST,
                    _safe_error(str(exc), limit=500),
                )
            except Exception as exc:
                self._json_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _safe_error(str(exc), limit=500),
                )
            return
        match = re.fullmatch(r"/api/reports/([^/]+)", parsed.path)
        if not match:
            self._json_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")
            return
        try:
            if self.app.report_set_purger is None:
                self._json_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "Exclusão permanente indisponível sem o banco configurado.",
                )
                return
            payload = self._request_json()
            run_id = unquote(match.group(1))
            result = self.app.report_set_purger.purge(
                run_id,
                actor=str(payload.get("actor") or ""),
                reason=str(payload.get("reason") or ""),
                confirmation=str(payload.get("confirmation") or ""),
                replacement_run_id=(
                    str(payload.get("replacement_run_id") or "").strip() or None
                ),
                allow_main_gap=payload.get("allow_main_gap") is True,
            )
            self._json(HTTPStatus.OK, result.to_dict())
        except (
            ActiveReportSetError,
            MainDeletionRequiresDecision,
            MainReportReplacementRequired,
            UnsafeReportSetPath,
        ) as exc:
            self._json_error(HTTPStatus.CONFLICT, _safe_error(str(exc), limit=500))
        except KeyError as exc:
            self._json_error(HTTPStatus.NOT_FOUND, _safe_error(str(exc), limit=500))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json_error(HTTPStatus.BAD_REQUEST, _safe_error(str(exc), limit=500))
        except ReportSetPurgeFinalizationError as exc:
            self._json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                _safe_error(str(exc), limit=500),
            )
        except Exception as exc:
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(str(exc), limit=500))

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._write_allowed():
            return
        parsed = urlsplit(self.path)
        client_match = re.fullmatch(r"/api/clients/([^/]+)", parsed.path)
        analyst_match = re.fullmatch(r"/api/analysts/([^/]+)", parsed.path)
        if not client_match and not analyst_match:
            self._json_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")
            return
        try:
            payload = self._request_json()
            if analyst_match:
                analyst = self.app.config.update_analyst(
                    unquote(analyst_match.group(1)),
                    payload,
                )
                self._json(HTTPStatus.OK, {"analyst": analyst})
                return
            client = self.app.update_client(
                unquote(client_match.group(1)),
                payload,
            )
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

    def _download_archive(self, archive: ReportArchiveResult) -> None:
        path = archive.path.resolve()
        if not path.is_file() or path.suffix.lower() != ".zip":
            path.unlink(missing_ok=True)
            self._json_error(HTTPStatus.NOT_FOUND, "Arquivo ZIP não encontrado no disco.")
            return
        raw_name = archive.download_name.replace(chr(34), "")
        ascii_name = (
            unicodedata.normalize("NFKD", raw_name)
            .encode("ascii", "ignore")
            .decode("ascii")
        ) or "Relatorios-Tenable.zip"
        encoded_name = quote(raw_name)
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}',
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with path.open("rb") as source:
                while chunk := source.read(64 * 1024):
                    self.wfile.write(chunk)
        finally:
            path.unlink(missing_ok=True)

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
    app = DashboardApplication(
        project_root=root,
        config_path=config,
        require_durable_batches=True,
    )
    server = DashboardHTTPServer((host, port), app)
    url = f"http://{host}:{port}"
    print(json.dumps({
        "status": "ready",
        "url": url,
        "config": str(app.config.config_path),
        "queue_mode": "staged_v1",
    }, ensure_ascii=False))
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
