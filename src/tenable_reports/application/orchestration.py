from __future__ import annotations

import json
import hashlib
import inspect
import os
import re
import shutil
import subprocess
import sys
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tenable_reports.application.publishing import write_json_atomic
from tenable_reports.application.retention import (
    RetentionPolicy,
    apply_retention,
    plan_tiered_retention,
)
from tenable_reports.application.failures import classify_failure
from tenable_reports.application.storage_guard import storage_preflight
from tenable_reports.config.profile import load_client_profile


SECRET_KEY_PARTS = (
    "access_key",
    "secret_key",
    "api_key",
    "api_secret",
    "token",
    "password",
    "tenable_access",
    "tenable_secret",
)
SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")


@dataclass(frozen=True, slots=True)
class OrchestrationClient:
    client_id: str
    profile_path: Path
    env_file: Path
    enabled: bool
    tags: tuple[str, ...]
    include_output: bool
    include_software_vulns: bool
    mask_sensitive: bool


@dataclass(frozen=True, slots=True)
class OrchestrationConfig:
    source_path: Path
    orchestration_id: str
    output_root: Path
    template_path: Path
    assets_dir: Path
    database_env_file: Path
    max_parallel: int
    retention_days: int | None
    retry_max_attempts: int
    retry_delay_seconds: int
    minimum_free_gb: int
    failed_staging_days: int
    logs_days: int
    cleanup_after_publish: bool
    failed_raw_days: int
    successful_raw_days: int
    normalized_days: int
    documents_days: int
    remote_collection_workers: int
    local_build_workers: int
    remote_processing_timeout_seconds: int
    remote_progress_warning_seconds: int
    max_clients_per_batch: int
    clients: tuple[OrchestrationClient, ...]


@dataclass(frozen=True, slots=True)
class OrchestrationRequest:
    mode: str
    reference_at: str | None = None
    days: int | None = None
    start_at: str | None = None
    end_at: str | None = None
    selected_client_ids: tuple[str, ...] = ()
    max_parallel: int | None = None
    dry_run: bool = False
    apply_retention_policy: bool = True
    vm_selective_mode: str | None = None
    vm_export_strategy: str | None = None
    vm_export_uuid: str | None = None
    historical_source: str | None = None
    force_live_collection: bool = False
    was_failure_policy: str | None = None
    job_control_file: str | None = None


@dataclass(frozen=True, slots=True)
class ClientAttemptResult:
    logical_job_id: str
    run_id: str
    attempt_number: int
    origin: str
    status: str
    exit_code: int | None
    started_at: str | None
    ended_at: str | None
    duration_seconds: float | None
    error_code: str | None
    retryable: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_job_id": self.logical_job_id,
            "run_id": self.run_id,
            "attempt_number": self.attempt_number,
            "origin": self.origin,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ClientExecutionResult:
    client_id: str
    status: str
    exit_code: int | None
    command: tuple[str, ...]
    started_at: str | None
    ended_at: str | None
    duration_seconds: float | None
    payload: Mapping[str, Any] | None
    error: str | None
    log_path: Path
    attempts: tuple[ClientAttemptResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "command": list(self.command),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "payload": dict(self.payload) if self.payload is not None else None,
            "error": self.error,
            "log": str(self.log_path.resolve()),
            "attempts": [item.to_dict() for item in self.attempts],
        }


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    status: str
    orchestration_id: str
    run_id: str
    mode: str
    control_directory: Path
    manifest_path: Path
    notification_path: Path
    clients: tuple[ClientExecutionResult, ...]
    retention_candidates: tuple[Mapping[str, str], ...]
    retention_removed: tuple[str, ...]
    retention_skipped: tuple[Mapping[str, str], ...] = ()

    @property
    def failed_count(self) -> int:
        return sum(
            item.status not in {
                "COMPLETE",
                "COMPLETE_WITH_WARNINGS",
                "PLANNED",
                "WAITING_WAS_DECISION",
            }
            for item in self.clients
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "orchestration_id": self.orchestration_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "control_directory": str(self.control_directory.resolve()),
            "manifest": str(self.manifest_path.resolve()),
            "notifications": str(self.notification_path.resolve()),
            "client_count": len(self.clients),
            "succeeded": sum(
                item.status in {"COMPLETE", "COMPLETE_WITH_WARNINGS"}
                for item in self.clients
            ),
            "failed": self.failed_count,
            "planned": sum(item.status == "PLANNED" for item in self.clients),
            "waiting_was_decision": sum(
                item.status == "WAITING_WAS_DECISION" for item in self.clients
            ),
            "interrupted": sum(
                item.status == "INTERRUPTED" for item in self.clients
            ),
            "clients": [item.to_dict() for item in self.clients],
            "retention_candidates": list(self.retention_candidates),
            "retention_removed": list(self.retention_removed),
            "retention_skipped": list(self.retention_skipped),
        }


ProgressCallback = Callable[[Mapping[str, Any]], None]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _reject_embedded_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in SECRET_KEY_PARTS):
                raise ValueError(
                    f"Credencial embutida nao e permitida em {path}.{key}; use env_file."
                )
            _reject_embedded_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_embedded_secrets(child, f"{path}[{index}]")


def _resolve_path(base: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} deve ser um caminho nao vazio.")
    path = Path(value)
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _boolean(value: Any, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} deve ser booleano.")
    return value


def _bounded_integer(
    value: Any,
    *,
    field: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    actual = default if value is None else value
    if (
        not isinstance(actual, int)
        or isinstance(actual, bool)
        or not minimum <= actual <= maximum
    ):
        raise ValueError(
            f"{field} deve ser um inteiro entre {minimum} e {maximum}."
        )
    return actual


def load_orchestration_config(path: str | Path) -> OrchestrationConfig:
    source = Path(path).resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Nao foi possivel ler a orquestracao: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON de orquestracao invalido na linha {exc.lineno}.") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("A orquestracao deve conter um objeto JSON.")
    _reject_embedded_secrets(payload)
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version da orquestracao deve ser 1.")
    orchestration_id = payload.get("orchestration_id")
    if not isinstance(orchestration_id, str) or not SAFE_ID_PATTERN.fullmatch(
        orchestration_id
    ):
        raise ValueError("orchestration_id deve usar apenas letras, numeros, ponto, _ ou -.")
    defaults = payload.get("defaults") or {}
    if not isinstance(defaults, Mapping):
        raise ValueError("defaults deve ser um objeto JSON.")
    base = source.parent
    output_root = _resolve_path(base, defaults.get("output_root", "../data"), "output_root")
    template_path = _resolve_path(
        base,
        defaults.get("template", "../templates/corporate/base-v1.docx"),
        "template",
    )
    assets_dir = _resolve_path(
        base,
        defaults.get("assets_dir", "../templates/corporate/assets"),
        "assets_dir",
    )
    database_env_file = _resolve_path(
        base,
        defaults.get("database_env_file", "../credentials/database.env"),
        "database_env_file",
    )
    max_parallel = defaults.get("max_parallel", 2)
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or not 1 <= max_parallel <= 8:
        raise ValueError("max_parallel deve ser um inteiro entre 1 e 8.")
    retention_days = defaults.get("retention_days")
    if retention_days is not None and (
        not isinstance(retention_days, int)
        or isinstance(retention_days, bool)
        or retention_days < 1
    ):
        raise ValueError("retention_days deve ser nulo ou um inteiro maior ou igual a 1.")
    retry_max_attempts = _bounded_integer(
        defaults.get("retry_max_attempts"),
        field="defaults.retry_max_attempts",
        default=2,
        minimum=1,
        maximum=2,
    )
    retry_delay_seconds = _bounded_integer(
        defaults.get("retry_delay_seconds"),
        field="defaults.retry_delay_seconds",
        default=900,
        minimum=0,
        maximum=86400,
    )
    minimum_free_gb = _bounded_integer(
        defaults.get("minimum_free_gb"),
        field="defaults.minimum_free_gb",
        default=10,
        minimum=1,
        maximum=1024,
    )
    failed_staging_days = _bounded_integer(
        defaults.get("failed_staging_days", defaults.get("failed_raw_days")),
        field="defaults.failed_staging_days",
        default=7,
        minimum=1,
        maximum=3650,
    )
    successful_raw_days = _bounded_integer(
        defaults.get("successful_raw_days"),
        field="defaults.successful_raw_days",
        default=60,
        minimum=1,
        maximum=3650,
    )
    normalized_days = _bounded_integer(
        defaults.get("normalized_days"),
        field="defaults.normalized_days",
        default=90,
        minimum=1,
        maximum=3650,
    )
    documents_days = _bounded_integer(
        defaults.get("documents_days"),
        field="defaults.documents_days",
        default=int(retention_days or 395),
        minimum=1,
        maximum=3650,
    )
    logs_days = _bounded_integer(
        defaults.get("logs_days"),
        field="defaults.logs_days",
        default=90,
        minimum=1,
        maximum=3650,
    )
    cleanup_after_publish = _boolean(
        defaults.get("cleanup_after_publish"),
        field="defaults.cleanup_after_publish",
        default=True,
    )
    remote_collection_workers = _bounded_integer(
        defaults.get("remote_collection_workers"),
        field="defaults.remote_collection_workers",
        default=0,
        minimum=0,
        maximum=64,
    )
    local_build_workers = _bounded_integer(
        defaults.get("local_build_workers"),
        field="defaults.local_build_workers",
        default=1,
        minimum=1,
        maximum=1,
    )
    remote_processing_timeout_seconds = _bounded_integer(
        defaults.get("remote_processing_timeout_seconds"),
        field="defaults.remote_processing_timeout_seconds",
        default=7200,
        minimum=60,
        maximum=86400,
    )
    remote_progress_warning_seconds = _bounded_integer(
        defaults.get("remote_progress_warning_seconds"),
        field="defaults.remote_progress_warning_seconds",
        default=900,
        minimum=60,
        maximum=86400,
    )
    if remote_progress_warning_seconds >= remote_processing_timeout_seconds:
        raise ValueError(
            "remote_progress_warning_seconds deve ser menor que "
            "remote_processing_timeout_seconds."
        )
    max_clients_per_batch = _bounded_integer(
        defaults.get("max_clients_per_batch"),
        field="defaults.max_clients_per_batch",
        default=64,
        minimum=1,
        maximum=64,
    )
    default_include_output = _boolean(
        defaults.get("include_output"), field="defaults.include_output", default=False
    )
    default_include_software = _boolean(
        defaults.get("include_software_vulns"),
        field="defaults.include_software_vulns",
        default=False,
    )
    default_mask = _boolean(
        defaults.get("mask_sensitive"), field="defaults.mask_sensitive", default=False
    )
    raw_clients = payload.get("clients")
    if not isinstance(raw_clients, list) or not raw_clients:
        raise ValueError("clients deve conter pelo menos um cliente.")
    clients: list[OrchestrationClient] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_clients):
        if not isinstance(raw, Mapping):
            raise ValueError(f"clients[{index}] deve ser um objeto JSON.")
        client_id = raw.get("client_id")
        if not isinstance(client_id, str) or not SAFE_ID_PATTERN.fullmatch(client_id):
            raise ValueError(f"clients[{index}].client_id e invalido.")
        if client_id in seen:
            raise ValueError(f"client_id duplicado na orquestracao: {client_id}")
        seen.add(client_id)
        profile_path = _resolve_path(base, raw.get("profile"), f"clients[{index}].profile")
        profile = load_client_profile(profile_path)
        if profile.client_id != client_id:
            raise ValueError(
                f"client_id {client_id} difere do perfil ({profile.client_id})."
            )
        env_file = _resolve_path(base, raw.get("env_file"), f"clients[{index}].env_file")
        tags = raw.get("tags") or []
        if not isinstance(tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in tags
        ):
            raise ValueError(f"clients[{index}].tags deve ser uma lista de textos.")
        clients.append(
            OrchestrationClient(
                client_id=client_id,
                profile_path=profile_path,
                env_file=env_file,
                enabled=_boolean(
                    raw.get("enabled"), field=f"clients[{index}].enabled", default=True
                ),
                tags=tuple(tags),
                include_output=_boolean(
                    raw.get("include_output"),
                    field=f"clients[{index}].include_output",
                    default=default_include_output,
                ),
                include_software_vulns=_boolean(
                    raw.get("include_software_vulns"),
                    field=f"clients[{index}].include_software_vulns",
                    default=default_include_software,
                ),
                mask_sensitive=_boolean(
                    raw.get("mask_sensitive"),
                    field=f"clients[{index}].mask_sensitive",
                    default=default_mask,
                ),
            )
        )
    return OrchestrationConfig(
        source_path=source,
        orchestration_id=orchestration_id,
        output_root=output_root,
        template_path=template_path,
        assets_dir=assets_dir,
        database_env_file=database_env_file,
        max_parallel=max_parallel,
        retention_days=retention_days,
        retry_max_attempts=retry_max_attempts,
        retry_delay_seconds=retry_delay_seconds,
        minimum_free_gb=minimum_free_gb,
        failed_staging_days=failed_staging_days,
        logs_days=logs_days,
        cleanup_after_publish=cleanup_after_publish,
        failed_raw_days=failed_staging_days,
        successful_raw_days=successful_raw_days,
        normalized_days=normalized_days,
        documents_days=documents_days,
        remote_collection_workers=remote_collection_workers,
        local_build_workers=local_build_workers,
        remote_processing_timeout_seconds=remote_processing_timeout_seconds,
        remote_progress_warning_seconds=remote_progress_warning_seconds,
        max_clients_per_batch=max_clients_per_batch,
        clients=tuple(clients),
    )


def resolve_remote_worker_capacity(
    *,
    eligible_client_count: int,
    configured_workers: int,
    max_clients_per_batch: int,
) -> int:
    eligible = max(1, int(eligible_client_count))
    limit = max(1, min(64, int(max_clients_per_batch)))
    configured = int(configured_workers)
    if configured < 0 or configured > 64:
        raise ValueError("remote_collection_workers deve estar entre 0 e 64.")
    requested = eligible if configured == 0 else configured
    return max(1, min(requested, eligible, limit))


def _validate_request(request: OrchestrationRequest) -> None:
    if request.mode not in {"automatic", "manual"}:
        raise ValueError("mode deve ser automatic ou manual.")
    has_start = request.start_at is not None
    has_end = request.end_at is not None
    if has_start != has_end:
        raise ValueError("--start-at e --end-at devem ser informados juntos.")
    if request.mode == "automatic" and any(
        value is not None for value in (request.days, request.start_at, request.end_at)
    ):
        raise ValueError("--days/--start-at/--end-at pertencem ao modo manual.")
    if request.days is not None and has_start:
        raise ValueError("--days nao pode ser combinado com --start-at/--end-at.")
    if request.vm_selective_mode not in {None, "disabled", "validation", "enabled"}:
        raise ValueError(
            "vm_selective_mode deve ser disabled, validation ou enabled."
        )
    if request.vm_export_strategy not in {None, "combined", "split"}:
        raise ValueError(
            "vm_export_strategy deve ser combined ou split."
        )
    if request.vm_export_uuid and len(request.selected_client_ids) != 1:
        raise ValueError(
            "vm_export_uuid exige exatamente um cliente selecionado."
        )
    if request.historical_source not in {None, "legacy", "inventory-beta"}:
        raise ValueError(
            "historical_source deve ser legacy ou inventory-beta."
        )
    if request.was_failure_policy not in {
        None,
        "wait",
        "continue",
        "retry_then_continue",
    }:
        raise ValueError(
            "was_failure_policy deve ser wait, continue ou retry_then_continue."
        )
    if (
        request.job_control_file is not None
        and not str(request.job_control_file).strip()
    ):
        raise ValueError("job_control_file nao pode ser vazio.")


def _select_clients(
    config: OrchestrationConfig,
    requested: Sequence[str],
) -> tuple[OrchestrationClient, ...]:
    enabled = {item.client_id: item for item in config.clients if item.enabled}
    if requested:
        unknown = sorted(set(requested) - set(enabled))
        if unknown:
            raise ValueError(
                "Clientes inexistentes ou desabilitados: " + ", ".join(unknown)
            )
        return tuple(enabled[client_id] for client_id in requested)
    if not enabled:
        raise ValueError("A orquestracao nao possui clientes habilitados.")
    return tuple(enabled.values())


def build_client_command(
    *,
    config: OrchestrationConfig,
    client: OrchestrationClient,
    request: OrchestrationRequest,
    client_run_id: str,
    logical_job_id: str | None = None,
    attempt_number: int = 1,
    origin: str | None = None,
) -> tuple[str, ...]:
    actual_origin = origin or ("SCHEDULED" if request.mode == "automatic" else "MANUAL")
    was_failure_policy = request.was_failure_policy or (
        "retry_then_continue" if request.mode == "automatic" else "wait"
    )
    command = [
        sys.executable,
        "-m",
        "tenable_reports",
        "run-client",
        "--mode",
        request.mode,
        "--profile",
        str(client.profile_path),
        "--env-file",
        str(client.env_file),
        "--database-env-file",
        str(config.database_env_file),
        "--output-root",
        str(config.output_root),
        "--template",
        str(config.template_path),
        "--assets-dir",
        str(config.assets_dir),
        "--run-id",
        client_run_id,
        "--logical-job-id",
        logical_job_id or client_run_id,
        "--attempt-number",
        str(attempt_number),
        "--origin",
        actual_origin,
        "--minimum-free-gb",
        str(config.minimum_free_gb),
        "--confirm-live-api",
        "--was-failure-policy",
        was_failure_policy,
    ]
    if request.job_control_file:
        command.extend(("--job-control-file", str(request.job_control_file)))
    if request.force_live_collection:
        command.append("--force-live-collection")
    if request.vm_selective_mode:
        command.extend(("--vm-selective-mode", request.vm_selective_mode))
    if request.vm_export_strategy:
        command.extend(("--vm-export-strategy", request.vm_export_strategy))
    if request.vm_export_uuid:
        command.extend(("--vm-export-uuid", request.vm_export_uuid))
    if request.historical_source:
        command.extend(("--historical-source", request.historical_source))
    if request.reference_at:
        command.extend(("--reference-at", request.reference_at))
    if request.days is not None:
        command.extend(("--days", str(request.days)))
    if request.start_at:
        command.extend(("--start-at", request.start_at, "--end-at", request.end_at or ""))
    for tag in client.tags:
        command.extend(("--tag", tag))
    if client.include_output:
        command.append("--include-output")
    if client.include_software_vulns:
        command.append("--include-software-vulns")
    if client.mask_sensitive:
        command.append("--mask-sensitive")
    if not config.cleanup_after_publish:
        command.append("--no-cleanup-after-publish")
    return tuple(command)


def _default_runner(
    command: Sequence[str],
    working_directory: Path,
    progress_callback: ProgressCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    child_environment = os.environ.copy()
    child_environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    process = subprocess.Popen(
        list(command),
        cwd=working_directory,
        env=child_environment,
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
                and event.get("event") in {
                    "TAG_REPORT_PROGRESS", "TENABLE_EXPORT_PROGRESS",
                }
            ):
                try:
                    progress_callback(event)
                except Exception:
                    pass
    return_code = process.wait()
    stderr_thread.join()
    return subprocess.CompletedProcess(
        list(command),
        return_code,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )


def _run_with_optional_progress(
    runner: CommandRunner,
    command: Sequence[str],
    working_directory: Path,
    progress_callback: ProgressCallback | None,
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
        return runner(
            command,
            working_directory,
            progress_callback=progress_callback,
        )
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
        return runner(command, working_directory, progress_callback)
    return runner(command, working_directory)


def _safe_error(value: str) -> str:
    redacted = re.sub(
        r"(?i)((?:tenable_(?:access|secret)|api[_-]?(?:key|secret)|token|password)\s*[=:]\s*)\S+",
        r"\1[REDACTED]",
        value,
    )
    return redacted.strip()[-4000:]


def _payload_from_stdout(stdout: str) -> Mapping[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("O processo do cliente nao retornou o JSON final.")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ValueError("O JSON final do processo do cliente e invalido.") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("O processo do cliente retornou um JSON inesperado.")
    return payload


def _write_client_log(
    path: Path,
    events: Sequence[Mapping[str, Any]],
    *,
    append: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as stream:
        stream.write(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
        )


def _execute_client(
    *,
    client: OrchestrationClient,
    command: tuple[str, ...],
    log_path: Path,
    working_directory: Path,
    runner: CommandRunner,
    logical_job_id: str,
    run_id: str,
    attempt_number: int,
    origin: str,
    progress_callback: ProgressCallback | None = None,
    append_log: bool = False,
) -> ClientExecutionResult:
    started = datetime.now(timezone.utc)
    monotonic_started = time.monotonic()
    events: list[Mapping[str, Any]] = [{
        "timestamp": started.isoformat(),
        "event": "CLIENT_STARTED",
        "client_id": client.client_id,
        "logical_job_id": logical_job_id,
        "run_id": run_id,
        "attempt_number": attempt_number,
        "origin": origin,
        "command": list(command),
    }]
    error_code: str | None = None
    retryable = False
    try:
        def forward_progress(event: Mapping[str, Any]) -> None:
            payload = dict(event)
            payload.setdefault("client_id", client.client_id)
            events.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **payload,
            })
            if progress_callback is None:
                return
            try:
                progress_callback(payload)
            except Exception:
                pass

        completed = _run_with_optional_progress(
            runner,
            command,
            working_directory,
            forward_progress,
        )
        ended = datetime.now(timezone.utc)
        duration = round(time.monotonic() - monotonic_started, 3)
        if completed.returncode != 0:
            failure_input: Any = completed.stderr or completed.stdout
            stdout_payload: Mapping[str, Any] | None = None
            if completed.stdout.strip():
                try:
                    stdout_payload = _payload_from_stdout(completed.stdout)
                except ValueError:
                    pass
                else:
                    stdout_status = str(
                        stdout_payload.get("status") or ""
                    ).strip().lower()
                    if (
                        stdout_payload.get("error_code")
                        or stdout_payload.get("error")
                        or stdout_payload.get("message")
                        or stdout_status in {"error", "failed", "failure"}
                    ):
                        failure_input = stdout_payload
            stdout_status = str(
                (stdout_payload or {}).get("status") or ""
            ).strip().lower()
            if completed.returncode == 130 or stdout_status == "interrupted":
                error_code = "INTERRUPTED_BY_USER"
                events.append({
                    "timestamp": ended.isoformat(),
                    "event": "CLIENT_INTERRUPTED",
                    "client_id": client.client_id,
                    "exit_code": completed.returncode,
                    "duration_seconds": duration,
                    "result": dict(stdout_payload or {}),
                })
                result = ClientExecutionResult(
                    client_id=client.client_id,
                    status="INTERRUPTED",
                    exit_code=completed.returncode,
                    command=command,
                    started_at=started.isoformat(),
                    ended_at=ended.isoformat(),
                    duration_seconds=duration,
                    payload=stdout_payload,
                    error=None,
                    log_path=log_path,
                )
            elif stdout_status == "waiting_was_decision":
                events.append({
                    "timestamp": ended.isoformat(),
                    "event": "CLIENT_WAITING_WAS_DECISION",
                    "client_id": client.client_id,
                    "exit_code": completed.returncode,
                    "duration_seconds": duration,
                    "result": dict(stdout_payload or {}),
                })
                result = ClientExecutionResult(
                    client_id=client.client_id,
                    status="WAITING_WAS_DECISION",
                    exit_code=completed.returncode,
                    command=command,
                    started_at=started.isoformat(),
                    ended_at=ended.isoformat(),
                    duration_seconds=duration,
                    payload=stdout_payload,
                    error=None,
                    log_path=log_path,
                )
            else:
                failure = classify_failure(failure_input)
                error = _safe_error(failure.message)
                error_code = failure.code.value
                retryable = failure.retryable
                events.append({
                    "timestamp": ended.isoformat(),
                    "event": "CLIENT_FAILED",
                    "client_id": client.client_id,
                    "exit_code": completed.returncode,
                    "duration_seconds": duration,
                    "error_code": error_code,
                    "retryable": retryable,
                    "error": error,
                })
                result = ClientExecutionResult(
                    client_id=client.client_id,
                    status="FAILED",
                    exit_code=completed.returncode,
                    command=command,
                    started_at=started.isoformat(),
                    ended_at=ended.isoformat(),
                    duration_seconds=duration,
                    payload=None,
                    error=error,
                    log_path=log_path,
                )
        else:
            payload = _payload_from_stdout(completed.stdout)
            payload_status = str(payload.get("status") or "").strip().lower()
            client_status = (
                "COMPLETE_WITH_WARNINGS"
                if payload_status == "complete_with_warnings"
                else "COMPLETE"
            )
            events.append({
                "timestamp": ended.isoformat(),
                "event": (
                    "CLIENT_COMPLETED_WITH_WARNINGS"
                    if client_status == "COMPLETE_WITH_WARNINGS"
                    else "CLIENT_COMPLETED"
                ),
                "client_id": client.client_id,
                "exit_code": completed.returncode,
                "duration_seconds": duration,
                "result": dict(payload),
            })
            result = ClientExecutionResult(
                client_id=client.client_id,
                status=client_status,
                exit_code=completed.returncode,
                command=command,
                started_at=started.isoformat(),
                ended_at=ended.isoformat(),
                duration_seconds=duration,
                payload=payload,
                error=None,
                log_path=log_path,
            )
    except Exception as exc:  # A falha precisa ser isolada por cliente.
        ended = datetime.now(timezone.utc)
        duration = round(time.monotonic() - monotonic_started, 3)
        failure = classify_failure(exc)
        error = _safe_error(failure.message)
        error_code = failure.code.value
        retryable = failure.retryable
        events.append({
            "timestamp": ended.isoformat(),
            "event": "CLIENT_FAILED",
            "client_id": client.client_id,
            "duration_seconds": duration,
            "error_code": error_code,
            "retryable": retryable,
            "error": error,
        })
        result = ClientExecutionResult(
            client_id=client.client_id,
            status="FAILED",
            exit_code=None,
            command=command,
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            duration_seconds=duration,
            payload=None,
            error=error,
            log_path=log_path,
        )
    attempt = ClientAttemptResult(
        logical_job_id=logical_job_id,
        run_id=run_id,
        attempt_number=attempt_number,
        origin=origin,
        status=result.status,
        exit_code=result.exit_code,
        started_at=result.started_at,
        ended_at=result.ended_at,
        duration_seconds=result.duration_seconds,
        error_code=error_code,
        retryable=retryable,
        error=result.error,
    )
    result = replace(result, attempts=(attempt,))
    _write_client_log(log_path, events, append=append_log)
    return result


def _logical_job_id(
    *,
    config: OrchestrationConfig,
    client: OrchestrationClient,
    request: OrchestrationRequest,
    current: datetime,
) -> str:
    if request.mode == "automatic":
        reference = request.reference_at or current.astimezone(timezone.utc).isoformat()
        competence = reference[:7]
    else:
        competence = (
            f"{request.start_at}/{request.end_at}"
            if request.start_at and request.end_at
            else f"days={request.days or 30}@{request.reference_at or current.date().isoformat()}"
        )
    canonical = json.dumps({
        "orchestration_id": config.orchestration_id,
        "client_id": client.client_id,
        "mode": request.mode,
        "competence": competence,
    }, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{client.client_id}-{digest}"


def _execute_client_with_retry(
    *,
    config: OrchestrationConfig,
    client: OrchestrationClient,
    request: OrchestrationRequest,
    orchestration_run_id: str,
    logical_job_id: str,
    log_path: Path,
    working_directory: Path,
    scoped_root: Path,
    runner: CommandRunner,
    sleeper: Callable[[float], None],
    disk_usage: Callable[[Any], Any],
    last_success_bytes: int | None,
    progress_callback: ProgressCallback | None = None,
) -> ClientExecutionResult:
    max_attempts = config.retry_max_attempts if request.mode == "automatic" else 1
    attempts: list[ClientAttemptResult] = []
    last_result: ClientExecutionResult | None = None
    for attempt_number in range(1, max_attempts + 1):
        origin = (
            "AUTOMATIC_RETRY"
            if attempt_number > 1
            else ("SCHEDULED" if request.mode == "automatic" else "MANUAL")
        )
        client_run_id = (
            f"{orchestration_run_id}-{client.client_id}-attempt-{attempt_number}"
        )
        command = build_client_command(
            config=config,
            client=client,
            request=request,
            client_run_id=client_run_id,
            logical_job_id=logical_job_id,
            attempt_number=attempt_number,
            origin=origin,
        )
        try:
            storage_preflight(
                scoped_root,
                last_success_bytes=last_success_bytes,
                minimum_free_gb=config.minimum_free_gb,
                disk_usage=disk_usage,
            )
        except Exception as exc:
            failure = classify_failure(exc)
            timestamp = datetime.now(timezone.utc).isoformat()
            attempt = ClientAttemptResult(
                logical_job_id=logical_job_id,
                run_id=client_run_id,
                attempt_number=attempt_number,
                origin=origin,
                status="WAITING_RETRY",
                exit_code=None,
                started_at=timestamp,
                ended_at=timestamp,
                duration_seconds=0.0,
                error_code=failure.code.value,
                retryable=failure.retryable,
                error=_safe_error(failure.message),
            )
            attempts.append(attempt)
            _write_client_log(log_path, [{
                "timestamp": timestamp,
                "event": "CLIENT_WAITING_RETRY",
                "client_id": client.client_id,
                **attempt.to_dict(),
            }], append=attempt_number > 1)
            return ClientExecutionResult(
                client_id=client.client_id,
                status="WAITING_RETRY",
                exit_code=None,
                command=command,
                started_at=timestamp,
                ended_at=timestamp,
                duration_seconds=0.0,
                payload=None,
                error=attempt.error,
                log_path=log_path,
                attempts=tuple(attempts),
            )
        result = _execute_client(
            client=client,
            command=command,
            log_path=log_path,
            working_directory=working_directory,
            runner=runner,
            logical_job_id=logical_job_id,
            run_id=client_run_id,
            attempt_number=attempt_number,
            origin=origin,
            progress_callback=progress_callback,
            append_log=attempt_number > 1,
        )
        attempts.extend(result.attempts)
        last_result = replace(result, attempts=tuple(attempts))
        if result.status in {
            "COMPLETE",
            "COMPLETE_WITH_WARNINGS",
            "WAITING_WAS_DECISION",
            "INTERRUPTED",
        }:
            return last_result
        current_attempt = result.attempts[-1]
        if not current_attempt.retryable or attempt_number >= max_attempts:
            return last_result
        sleeper(float(config.retry_delay_seconds))
    if last_result is None:
        raise RuntimeError("A orquestração não executou nenhuma tentativa.")
    return last_result


def run_orchestration(
    *,
    config: OrchestrationConfig,
    request: OrchestrationRequest,
    runner: CommandRunner | None = None,
    now: datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    disk_usage: Callable[[Any], Any] = shutil.disk_usage,
    last_success_bytes_by_client: Mapping[str, int] | None = None,
    run_status: Mapping[str, str] | None = None,
    history_confirmed_run_ids: Sequence[str] = (),
    main_run_ids: Sequence[str] = (),
    retry_required_run_ids: Sequence[str] = (),
    progress_callback: ProgressCallback | None = None,
) -> OrchestrationResult:
    _validate_request(request)
    clients = _select_clients(config, request.selected_client_ids)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    run_id = f"{current.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    scope_name = "automatic-monthly" if request.mode == "automatic" else "manual"
    scoped_root = config.output_root / scope_name
    control_directory = (
        scoped_root / "orchestration" / config.orchestration_id / run_id
    )
    logs_directory = control_directory / "clients"
    control_directory.mkdir(parents=True, exist_ok=False)
    jobs: list[
        tuple[OrchestrationClient, tuple[str, ...], Path, str, str]
    ] = []
    for client in clients:
        logical_job_id = _logical_job_id(
            config=config,
            client=client,
            request=request,
            current=current,
        )
        client_run_id = f"{run_id}-{client.client_id}-attempt-1"
        command = build_client_command(
            config=config,
            client=client,
            request=request,
            client_run_id=client_run_id,
            logical_job_id=logical_job_id,
            attempt_number=1,
            origin="SCHEDULED" if request.mode == "automatic" else "MANUAL",
        )
        jobs.append(
            (
                client,
                command,
                logs_directory / f"{client.client_id}.jsonl",
                client_run_id,
                logical_job_id,
            )
        )
    if request.dry_run:
        results = tuple(
            ClientExecutionResult(
                client_id=client.client_id,
                status="PLANNED",
                exit_code=None,
                command=command,
                started_at=None,
                ended_at=None,
                duration_seconds=None,
                payload=None,
                error=None,
                log_path=log_path,
            )
            for client, command, log_path, _, _ in jobs
        )
        for result in results:
            _write_client_log(result.log_path, [{
                "timestamp": current.isoformat(),
                "event": "CLIENT_PLANNED",
                "client_id": result.client_id,
                "command": list(result.command),
            }])
    else:
        command_runner = runner or _default_runner
        max_parallel = (
            request.max_parallel
            if request.max_parallel is not None
            else config.max_parallel
        )
        if not 1 <= max_parallel <= 8:
            raise ValueError("max_parallel deve ser um inteiro entre 1 e 8.")
        result_by_client: dict[str, ClientExecutionResult] = {}
        with ThreadPoolExecutor(max_workers=min(max_parallel, len(jobs))) as executor:
            future_map = {
                executor.submit(
                    _execute_client_with_retry,
                    config=config,
                    client=client,
                    request=request,
                    orchestration_run_id=run_id,
                    logical_job_id=logical_job_id,
                    log_path=log_path,
                    working_directory=config.source_path.parent,
                    scoped_root=scoped_root,
                    runner=command_runner,
                    sleeper=sleeper,
                    disk_usage=disk_usage,
                    last_success_bytes=(last_success_bytes_by_client or {}).get(
                        client.client_id
                    ),
                    progress_callback=progress_callback,
                ): client.client_id
                for client, _, log_path, _, logical_job_id in jobs
            }
            for future in as_completed(future_map):
                result_by_client[future_map[future]] = future.result()
        results = tuple(result_by_client[client.client_id] for client, *_ in jobs)
    protected_run_ids = tuple(
        attempt.run_id
        for result in results
        for attempt in result.attempts
    ) or tuple(job[3] for job in jobs)
    retention_plan = plan_tiered_retention(
        scoped_output_root=scoped_root,
        policy=RetentionPolicy(
            failed_raw_days=config.failed_raw_days,
            successful_raw_days=config.successful_raw_days,
            normalized_days=config.normalized_days,
            documents_days=config.documents_days,
        ),
        run_status=run_status,
        history_confirmed_run_ids=history_confirmed_run_ids,
        main_run_ids=main_run_ids,
        active_run_ids=protected_run_ids,
        retry_required_run_ids=retry_required_run_ids,
        now=current,
    )
    removed = (
        apply_retention(
            scoped_output_root=scoped_root,
            candidates=retention_plan.candidates,
        )
        if request.apply_retention_policy and not request.dry_run
        else ()
    )
    failed = sum(item.status == "FAILED" for item in results)
    interrupted = sum(item.status == "INTERRUPTED" for item in results)
    waiting_was = sum(item.status == "WAITING_WAS_DECISION" for item in results)
    warned = sum(item.status == "COMPLETE_WITH_WARNINGS" for item in results)
    status = (
        "DRY_RUN"
        if request.dry_run
        else (
            "PARTIAL_FAILURE"
            if failed
            else "INTERRUPTED" if interrupted
            else "WAITING_WAS_DECISION" if waiting_was
            else "COMPLETE_WITH_WARNINGS" if warned else "COMPLETE"
        )
    )
    manifest_path = control_directory / "orchestration-manifest.json"
    notification_path = control_directory / "notifications.jsonl"
    retention_payload = tuple(item.to_dict() for item in retention_plan.candidates)
    result = OrchestrationResult(
        status=status,
        orchestration_id=config.orchestration_id,
        run_id=run_id,
        mode=request.mode,
        control_directory=control_directory,
        manifest_path=manifest_path,
        notification_path=notification_path,
        clients=results,
        retention_candidates=retention_payload,
        retention_removed=tuple(str(path) for path in removed),
        retention_skipped=tuple(item.to_dict() for item in retention_plan.skipped),
    )
    write_json_atomic(manifest_path, result.to_dict())
    notifications = [
        {
            "timestamp": current.isoformat(),
            "event": f"CLIENT_{item.status}",
            "orchestration_id": config.orchestration_id,
            "run_id": run_id,
            "client_id": item.client_id,
            "payload": dict(item.payload) if item.payload else None,
            "error": item.error,
        }
        for item in results
    ]
    notifications.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": f"ORCHESTRATION_{status}",
        "orchestration_id": config.orchestration_id,
        "run_id": run_id,
        "clients": len(results),
        "failed": failed,
        "warnings": warned,
    })
    notification_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in notifications),
        encoding="utf-8",
    )
    return result
