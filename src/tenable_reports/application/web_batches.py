from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID

from tenable_reports.domain.web_batches import (
    BatchAction,
    BatchJobPhase,
    BatchJobStatus,
    WebBatch,
    WebBatchEvent,
    WebBatchJob,
)


class NoEligibleBatchJobsError(ValueError):
    def __init__(self, source_batch_id: UUID) -> None:
        self.source_batch_id = source_batch_id
        super().__init__(
            "O lote de origem nao possui clientes elegiveis para esta acao."
        )


class BatchConfirmationError(ValueError):
    pass


class BatchClientConflictError(RuntimeError):
    def __init__(self, client_ids: Sequence[str]) -> None:
        self.client_ids = tuple(sorted({str(item) for item in client_ids}))
        super().__init__(
            "Ha clientes com geracao ativa em outro lote: "
            + ", ".join(self.client_ids)
        )


@dataclass(frozen=True, slots=True)
class DerivedBatchRequest:
    source_batch_id: UUID
    kind: BatchAction
    idempotency_key: str
    confirmation_token: str | None = None
    actor: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {
            BatchAction.RETRY_INCOMPLETE,
            BatchAction.RERUN_ALL,
        }:
            raise ValueError("A derivacao exige RETRY_INCOMPLETE ou RERUN_ALL.")
        normalized_key = str(self.idempotency_key or "").strip()
        if not normalized_key:
            raise ValueError("A chave idempotente da derivacao e obrigatoria.")
        if len(normalized_key) > 200:
            raise ValueError("A chave idempotente da derivacao e muito longa.")
        object.__setattr__(self, "idempotency_key", normalized_key)


@dataclass(frozen=True, slots=True)
class BatchJobResult:
    status: BatchJobStatus
    exit_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


_CLAIMABLE_PHASES = frozenset(
    {
        BatchJobPhase.LEGACY,
        BatchJobPhase.REMOTE_QUEUED,
        BatchJobPhase.READY_FOR_BUILD,
    }
)


def normalize_claim_phases(
    phases: Sequence[BatchJobPhase] | None,
) -> tuple[BatchJobPhase, ...]:
    if phases is None:
        return (BatchJobPhase.LEGACY,)
    if isinstance(phases, (str, bytes)):
        raise ValueError("As fases de reivindicacao devem formar uma sequencia.")
    normalized: list[BatchJobPhase] = []
    for value in phases:
        try:
            phase = value if isinstance(value, BatchJobPhase) else BatchJobPhase(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Fase de reivindicacao invalida.") from exc
        if phase not in _CLAIMABLE_PHASES:
            raise ValueError("A fase informada nao pode ser reivindicada.")
        if phase not in normalized:
            normalized.append(phase)
    if not normalized:
        raise ValueError("Ao menos uma fase de reivindicacao e obrigatoria.")
    return tuple(normalized)


def claimed_job_phase(phase: BatchJobPhase) -> BatchJobPhase:
    return {
        BatchJobPhase.LEGACY: BatchJobPhase.LEGACY,
        BatchJobPhase.REMOTE_QUEUED: BatchJobPhase.REMOTE_RUNNING,
        BatchJobPhase.READY_FOR_BUILD: BatchJobPhase.BUILD_RUNNING,
    }[phase]


def validate_collection_checkpoint_path(value: str | Path | None) -> str:
    if value is None:
        raise ValueError("O checkpoint de coleta e obrigatorio.")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("O caminho do checkpoint de coleta e invalido.")
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError("O checkpoint de coleta nao foi encontrado.")
    return str(resolved)

_SENSITIVE_PAYLOAD_KEYS = frozenset({
    "access_key",
    "secret_key",
    "api_key",
    "api_secret",
    "api_token",
    "cloud_token",
    "token",
    "password",
    "credential",
    "credentials",
    "authorization",
    "bearer_token",
})


def assert_sanitized_payload(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).strip().casefold().replace("-", "_")
            if normalized_key in _SENSITIVE_PAYLOAD_KEYS:
                raise ValueError(
                    f"O campo {path}.{key} pode conter credencial e nao pode ser persistido."
                )
            assert_sanitized_payload(nested, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_sanitized_payload(nested, path=f"{path}[{index}]")


def build_manual_batch_options(
    *,
    clients: Sequence[Mapping[str, Any]],
    selected_client_ids: Sequence[str],
    selection_filter_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(clients, (str, bytes)) or not isinstance(clients, Sequence):
        raise ValueError("UNKNOWN_CLIENT_SELECTION: lista de clientes invalida.")
    if isinstance(selected_client_ids, (str, bytes)) or not isinstance(
        selected_client_ids, Sequence
    ):
        raise ValueError("UNKNOWN_CLIENT_SELECTION: selecao de clientes invalida.")

    normalized_selected: list[str] = []
    seen_selected: set[str] = set()
    for raw_client_id in selected_client_ids:
        if not isinstance(raw_client_id, str):
            raise ValueError("UNKNOWN_CLIENT_SELECTION: cliente invalido.")
        client_id = raw_client_id.strip()
        if not client_id:
            raise ValueError("UNKNOWN_CLIENT_SELECTION: cliente invalido.")
        if client_id in seen_selected:
            raise ValueError(
                f"DUPLICATE_CLIENT_SELECTION: cliente duplicado: {client_id}."
            )
        seen_selected.add(client_id)
        normalized_selected.append(client_id)
    if not normalized_selected:
        raise ValueError("EMPTY_CLIENT_SELECTION: selecione ao menos um cliente.")

    client_rows: dict[str, Mapping[str, Any]] = {}
    enabled_client_ids: list[str] = []
    for client in clients:
        if not isinstance(client, Mapping):
            continue
        raw_client_id = client.get("client_id")
        if not isinstance(raw_client_id, str):
            continue
        client_id = raw_client_id.strip()
        if not client_id or client_id in client_rows:
            continue
        client_rows[client_id] = client
        if client.get("enabled") is True:
            enabled_client_ids.append(client_id)

    for client_id in normalized_selected:
        client = client_rows.get(client_id)
        if client is None or client.get("enabled") is not True:
            raise ValueError(
                f"UNKNOWN_CLIENT_SELECTION: cliente desconhecido ou inativo: {client_id}."
            )

    raw_filter = {} if selection_filter_snapshot is None else selection_filter_snapshot
    if not isinstance(raw_filter, Mapping):
        raise ValueError("INVALID_SELECTION_FILTER: filtro deve ser um objeto.")
    allowed_filter_keys = {"analyst_id", "query", "unassigned"}
    if any(key not in allowed_filter_keys for key in raw_filter):
        raise ValueError("INVALID_SELECTION_FILTER: campo desconhecido.")
    normalized_filter: dict[str, Any] = {}
    if "analyst_id" in raw_filter:
        raw_analyst_id = raw_filter["analyst_id"]
        if raw_analyst_id is not None and not isinstance(raw_analyst_id, str):
            raise ValueError("INVALID_SELECTION_FILTER: analyst_id invalido.")
        normalized_filter["analyst_id"] = (
            raw_analyst_id.strip() or None
            if isinstance(raw_analyst_id, str)
            else None
        )
    if "query" in raw_filter:
        raw_query = raw_filter["query"]
        if not isinstance(raw_query, str):
            raise ValueError("INVALID_SELECTION_FILTER: query invalida.")
        query = raw_query.strip()
        if len(query) > 200:
            raise ValueError("INVALID_SELECTION_FILTER: query muito longa.")
        normalized_filter["query"] = query
    if "unassigned" in raw_filter:
        raw_unassigned = raw_filter["unassigned"]
        if not isinstance(raw_unassigned, bool):
            raise ValueError("INVALID_SELECTION_FILTER: unassigned invalido.")
        normalized_filter["unassigned"] = raw_unassigned

    analyst_snapshot: dict[str, dict[str, Any]] = {}
    for client_id in normalized_selected:
        client = client_rows[client_id]
        raw_analyst_id = client.get("responsible_analyst_id")
        analyst_id = (
            raw_analyst_id.strip() or None
            if isinstance(raw_analyst_id, str)
            else None
        )
        raw_display_name = client.get("responsible_analyst_name")
        display_name = (
            raw_display_name.strip() or None
            if analyst_id is not None and isinstance(raw_display_name, str)
            else None
        )
        analyst_snapshot[client_id] = {
            "analyst_id": analyst_id,
            "display_name": display_name,
            "active": (
                client.get("responsible_analyst_active") is True
                if analyst_id is not None
                else False
            ),
        }

    selected_set = set(normalized_selected)
    options = {
        "selected_client_ids": list(normalized_selected),
        "excluded_client_ids": [
            client_id
            for client_id in enabled_client_ids
            if client_id not in selected_set
        ],
        "analyst_snapshot_by_client": analyst_snapshot,
        "selection_filter_snapshot": deepcopy(normalized_filter),
    }
    assert_sanitized_payload(options)
    return options


class WebBatchRepository(Protocol):
    def create_batch(
        self,
        batch: WebBatch,
        jobs: Sequence[WebBatchJob],
    ) -> WebBatch: ...

    def get_batch(self, batch_id: UUID) -> WebBatch | None: ...

    def get_job(self, job_id: UUID) -> WebBatchJob | None: ...

    def list_batches(self, *, limit: int = 50) -> tuple[WebBatch, ...]: ...

    def list_batch_jobs(self, batch_id: UUID) -> tuple[WebBatchJob, ...]: ...

    def list_batch_jobs_for_batches(
        self,
        batch_ids: Sequence[UUID],
    ) -> Mapping[UUID, tuple[WebBatchJob, ...]]: ...

    def record_job_process(
        self,
        job_id: UUID,
        process_id: int,
        *,
        control_file: str | None = None,
    ) -> WebBatchJob: ...

    def record_vm_export_progress(
        self,
        job_id: UUID,
        *,
        export_uuid: str,
        resume_manifest_path: str | None,
        origin: str | None,
        remote_status: str,
        observed_at: str,
        progress_at: str | None,
        completed_chunks: int,
        total_chunks: int,
        persisted_chunks: Sequence[int],
        status_confirmed: bool = True,
    ) -> WebBatchJob: ...

    def record_vm_export_replacement(
        self,
        job_id: UUID,
        *,
        previous_export_uuid: str,
        replacement_export_uuid: str,
        resume_manifest_path: str | None,
        origin: str | None,
        observed_at: str,
    ) -> WebBatchJob: ...

    def request_action(
        self,
        batch_id: UUID,
        action: BatchAction,
        *,
        actor: str | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> WebBatch: ...

    def request_job_stop(
        self,
        job_id: UUID,
        *,
        actor: str | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> WebBatchJob: ...

    def active_client_conflicts(
        self,
        client_ids: Sequence[str],
        *,
        excluding_batch_id: UUID,
    ) -> tuple[str, ...]: ...

    def claim_next_job(
        self,
        *,
        worker_id: str,
        phases: Sequence[BatchJobPhase] | None = None,
    ) -> WebBatchJob | None: ...

    def advance_job_phase(
        self,
        job_id: UUID,
        *,
        expected_phase: BatchJobPhase,
        requested_phase: BatchJobPhase,
        collection_checkpoint_path: str | Path | None = None,
    ) -> WebBatchJob: ...

    def complete_job(self, job_id: UUID, result: BatchJobResult) -> None: ...

    def append_event(self, event: WebBatchEvent) -> None: ...

    def list_events(self, batch_id: UUID) -> tuple[WebBatchEvent, ...]: ...

    def list_events_for_batches(
        self,
        batch_ids: Sequence[UUID],
    ) -> Mapping[UUID, tuple[WebBatchEvent, ...]]: ...

    def reconcile_abandoned_jobs(
        self,
        *,
        active_worker_ids: set[str],
    ) -> int: ...
