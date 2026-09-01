from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID

from tenable_reports.domain.web_batches import (
    BatchAction,
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

class WebBatchRepository(Protocol):
    def create_batch(
        self,
        batch: WebBatch,
        jobs: Sequence[WebBatchJob],
    ) -> WebBatch: ...

    def get_batch(self, batch_id: UUID) -> WebBatch | None: ...

    def list_batches(self, *, limit: int = 50) -> tuple[WebBatch, ...]: ...

    def list_batch_jobs(self, batch_id: UUID) -> tuple[WebBatchJob, ...]: ...

    def record_job_process(
        self,
        job_id: UUID,
        process_id: int,
        *,
        control_file: str | None = None,
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

    def active_client_conflicts(
        self,
        client_ids: Sequence[str],
        *,
        excluding_batch_id: UUID,
    ) -> tuple[str, ...]: ...

    def claim_next_job(self, *, worker_id: str) -> WebBatchJob | None: ...

    def complete_job(self, job_id: UUID, result: BatchJobResult) -> None: ...

    def append_event(self, event: WebBatchEvent) -> None: ...

    def list_events(self, batch_id: UUID) -> tuple[WebBatchEvent, ...]: ...

    def reconcile_abandoned_jobs(
        self,
        *,
        active_worker_ids: set[str],
    ) -> int: ...
