"""Durable state for independently recoverable remote report components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
import re
from uuid import UUID

from tenable_reports.domain.report_components import ReportComponent


class RemoteComponentState(StrEnum):
    PENDING = "PENDING"
    RUNNING_WINDOW_1 = "RUNNING_WINDOW_1"
    RUNNING_WINDOW_2 = "RUNNING_WINDOW_2"
    RUNNING_WINDOW_3 = "RUNNING_WINDOW_3"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    WAITING_MANUAL_RETRY = "WAITING_MANUAL_RETRY"
    NON_RETRYABLE_FAILURE = "NON_RETRYABLE_FAILURE"
    INTERRUPTED = "INTERRUPTED"


class RemoteIdentifierKind(StrEnum):
    UUID = "UUID"
    CURSOR = "CURSOR"
    DATASET = "DATASET"


class RemoteObservationKind(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    TERMINAL_RETRYABLE_FAILURE = "TERMINAL_RETRYABLE_FAILURE"
    NON_RETRYABLE_FAILURE = "NON_RETRYABLE_FAILURE"


_FAILURE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,99}$")
_SENSITIVE_FAILURE_PATTERN = re.compile(
    r"(?i)\b(?:access_key|secret_key|api_key|api_secret|api_token|"
    r"cloud_token|token|password|authorization|bearer_token)\s*[:=]"
)
_UTC_FIELDS = (
    "deadline_at",
    "last_contact_at",
    "last_progress_at",
    "lease_expires_at",
    "created_at",
    "started_at",
    "ended_at",
)


def _require_utc(value: datetime | None, *, field_name: str) -> None:
    if value is None:
        return
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} deve usar timezone UTC.")


def _validate_failure_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if not _FAILURE_CODE_PATTERN.fullmatch(normalized):
        raise ValueError("failure_code deve usar somente A-Z, 0-9 e underscore.")
    return normalized


@dataclass(frozen=True, slots=True)
class RemoteComponentWindow:
    id: UUID
    batch_job_id: UUID
    component: ReportComponent
    state: RemoteComponentState
    window_number: int
    attempt_number: int
    origin: str
    deadline_at: datetime
    parent_component_id: UUID | None = None
    replacement_created_in_window_2: bool = False
    replacement_created_in_window_3: bool = False
    identifier_kind: RemoteIdentifierKind | None = None
    remote_identifier: str | None = None
    identifier_origin: str | None = None
    query_fingerprint: str | None = None
    checkpoint_path: str | None = None
    completed_units: int = 0
    total_units: int | None = None
    last_remote_status: str | None = None
    last_contact_at: datetime | None = None
    last_progress_at: datetime | None = None
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    retryable: bool = False
    created_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.window_number not in {1, 2, 3}:
            raise ValueError("window_number deve estar entre 1 e 3.")
        if self.attempt_number < 1:
            raise ValueError("attempt_number deve ser maior ou igual a 1.")
        if self.replacement_created_in_window_2 and self.window_number < 2:
            raise ValueError("A substituição da Janela 2 não existe na Janela 1.")
        if self.replacement_created_in_window_3 and self.window_number != 3:
            raise ValueError("A substituição da Janela 3 só existe na Janela 3.")
        for field_name in _UTC_FIELDS:
            _require_utc(getattr(self, field_name), field_name=field_name)

        origin = str(self.origin or "").strip()
        if not origin:
            raise ValueError("origin não pode ser vazio.")
        identifier = (
            str(self.remote_identifier).strip()
            if self.remote_identifier is not None
            else None
        )
        if identifier == "":
            identifier = None
        if identifier is not None and self.identifier_kind is None:
            raise ValueError("identifier_kind é obrigatório para um identificador.")
        if identifier is None and self.identifier_kind is not None:
            raise ValueError("remote_identifier é obrigatório para identifier_kind.")
        if identifier is not None and self.identifier_kind is RemoteIdentifierKind.UUID:
            try:
                UUID(identifier)
            except ValueError as exc:
                raise ValueError("remote_identifier não contém um UUID válido.") from exc

        if self.completed_units < 0:
            raise ValueError("completed_units não pode ser negativo.")
        if self.total_units is not None:
            if self.total_units < 0:
                raise ValueError("total_units não pode ser negativo.")
            if self.completed_units > self.total_units:
                raise ValueError("completed_units não pode superar total_units.")
        if self.checkpoint_path is not None:
            checkpoint = Path(str(self.checkpoint_path))
            if not checkpoint.is_absolute() or ".." in checkpoint.parts:
                raise ValueError("checkpoint_path deve ser absoluto e seguro.")

        failure_code = _validate_failure_code(self.failure_code)
        failure_states = {
            RemoteComponentState.WAITING_MANUAL_RETRY,
            RemoteComponentState.NON_RETRYABLE_FAILURE,
            RemoteComponentState.INTERRUPTED,
        }
        if self.state in failure_states and failure_code is None:
            raise ValueError("failure_code é obrigatório para estado de falha.")
        if self.retryable and self.state not in {
            RemoteComponentState.WAITING_MANUAL_RETRY,
            RemoteComponentState.INTERRUPTED,
        }:
            raise ValueError("retryable é incompatível com o estado do componente.")
        if self.failure_message is not None:
            message = str(self.failure_message).strip()
            if (
                "\n" in message
                or "\r" in message
                or len(message) > 500
                or _SENSITIVE_FAILURE_PATTERN.search(message)
            ):
                raise ValueError("failure_message deve ser sanitizada e ter uma linha.")
            object.__setattr__(self, "failure_message", message)

        object.__setattr__(self, "component", ReportComponent(self.component))
        object.__setattr__(self, "state", RemoteComponentState(self.state))
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "remote_identifier", identifier)
        object.__setattr__(self, "failure_code", failure_code)


@dataclass(frozen=True, slots=True)
class RemoteObservation:
    kind: RemoteObservationKind
    completed_units: int = 0
    total_units: int | None = None
    remote_status: str | None = None
    failure_code: str | None = None
    valid_empty: bool = False

    def __post_init__(self) -> None:
        if self.completed_units < 0:
            raise ValueError("completed_units não pode ser negativo.")
        if self.total_units is not None:
            if self.total_units < 0 or self.completed_units > self.total_units:
                raise ValueError("total_units é incompatível com completed_units.")
        failure_code = _validate_failure_code(self.failure_code)
        failure_kinds = {
            RemoteObservationKind.INVALID_IDENTIFIER,
            RemoteObservationKind.TRANSIENT_FAILURE,
            RemoteObservationKind.TERMINAL_RETRYABLE_FAILURE,
            RemoteObservationKind.NON_RETRYABLE_FAILURE,
            RemoteObservationKind.NOT_APPLICABLE,
        }
        if self.kind in failure_kinds and failure_code is None:
            raise ValueError("failure_code é obrigatório para esta observação.")
        if self.kind not in failure_kinds and failure_code is not None:
            raise ValueError("failure_code é incompatível com esta observação.")
        if self.valid_empty and self.kind is not RemoteObservationKind.COMPLETE:
            raise ValueError("valid_empty só é permitido para resultado completo.")
        object.__setattr__(self, "kind", RemoteObservationKind(self.kind))
        object.__setattr__(self, "failure_code", failure_code)

    @classmethod
    def processing(
        cls,
        *,
        completed: int,
        total: int | None,
        remote_status: str = "PROCESSING",
    ) -> RemoteObservation:
        return cls(
            kind=RemoteObservationKind.PROCESSING,
            completed_units=completed,
            total_units=total,
            remote_status=remote_status,
        )

    @classmethod
    def complete(
        cls,
        *,
        completed: int,
        total: int,
        valid_empty: bool = False,
    ) -> RemoteObservation:
        return cls(
            kind=RemoteObservationKind.COMPLETE,
            completed_units=completed,
            total_units=total,
            remote_status="FINISHED",
            valid_empty=valid_empty,
        )

    @classmethod
    def not_applicable(cls, *, code: str) -> RemoteObservation:
        return cls(kind=RemoteObservationKind.NOT_APPLICABLE, failure_code=code)

    @classmethod
    def invalid_identifier(cls, *, code: str) -> RemoteObservation:
        return cls(
            kind=RemoteObservationKind.INVALID_IDENTIFIER,
            failure_code=code,
        )

    @classmethod
    def transient_failure(cls, *, code: str) -> RemoteObservation:
        return cls(kind=RemoteObservationKind.TRANSIENT_FAILURE, failure_code=code)

    @classmethod
    def terminal_retryable_failure(cls, *, code: str) -> RemoteObservation:
        return cls(
            kind=RemoteObservationKind.TERMINAL_RETRYABLE_FAILURE,
            failure_code=code,
        )

    @classmethod
    def non_retryable_failure(cls, *, code: str) -> RemoteObservation:
        return cls(
            kind=RemoteObservationKind.NON_RETRYABLE_FAILURE,
            failure_code=code,
        )


__all__ = [
    "RemoteComponentState",
    "RemoteComponentWindow",
    "RemoteIdentifierKind",
    "RemoteObservation",
    "RemoteObservationKind",
]
