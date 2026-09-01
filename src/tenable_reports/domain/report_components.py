"""Domain model for independently retryable report components."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import UUID


class ReportComponent(StrEnum):
    VM_CORE = "VM_CORE"
    WAS = "WAS"
    CLOUD = "CLOUD"


class ComponentStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    SKIPPED = "SKIPPED"


class ComponentStage(StrEnum):
    COLLECTION = "COLLECTION"
    DATASET = "DATASET"
    RENDER = "RENDER"
    DOCUMENT_VALIDATION = "DOCUMENT_VALIDATION"
    SNAPSHOT_PUBLICATION = "SNAPSHOT_PUBLICATION"
    REPORT_PUBLICATION = "REPORT_PUBLICATION"


class ComponentSetStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"


_AVAILABLE_STATUSES = frozenset({
    ComponentStatus.COMPLETE,
    ComponentStatus.COMPLETE_WITH_WARNINGS,
})
_FAILED_STATUSES = frozenset({
    ComponentStatus.FAILED,
    ComponentStatus.INTERRUPTED,
})
_PENDING_STATUSES = frozenset({
    ComponentStatus.PENDING,
    ComponentStatus.RUNNING,
})
_FAILURE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,99}$")
_SENSITIVE_FAILURE_PATTERN = re.compile(
    r"(?i)\b(?:access_key|secret_key|api_key|api_secret|api_token|"
    r"cloud_token|token|password|authorization|bearer_token)\s*[:=]"
)


class _FrozenSequence(tuple):
    def __new__(cls, values: Sequence[Any]) -> _FrozenSequence:
        return super().__new__(cls, values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return tuple(self) == tuple(other)
        return NotImplemented

    __hash__ = tuple.__hash__


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("Objetos JSON exigem chaves textuais.")
            frozen[key] = _freeze_json(nested)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return _FrozenSequence(tuple(_freeze_json(item) for item in value))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("artifact_references deve conter somente valores JSON.")


@dataclass(frozen=True, slots=True)
class ComponentAttempt:
    id: UUID
    client_id: str
    source_run_id: str
    component: ReportComponent
    status: ComponentStatus
    stage: ComponentStage
    attempt_number: int
    retryable: bool = False
    failure_code: str | None = None
    failure_message: str | None = None
    checkpoint_path: str | None = None
    artifact_references: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None

    def __post_init__(self) -> None:
        client_id = str(self.client_id or "").strip()
        source_run_id = str(self.source_run_id or "").strip()
        if not client_id:
            raise ValueError("client_id não pode ser vazio.")
        if not source_run_id:
            raise ValueError("source_run_id não pode ser vazio.")
        if self.attempt_number < 1:
            raise ValueError("attempt_number deve ser maior ou igual a 1.")
        if self.retryable and self.status not in _FAILED_STATUSES:
            raise ValueError(
                "retryable só pode ser verdadeiro para FAILED ou INTERRUPTED."
            )
        if self.status in _FAILED_STATUSES:
            if not self.failure_code:
                raise ValueError(
                    "failure_code é obrigatório para FAILED ou INTERRUPTED."
                )
        elif self.failure_code is not None or self.failure_message is not None:
            raise ValueError(
                "failure_code e failure_message são incompatíveis com este status."
            )
        if self.failure_code is not None and not _FAILURE_CODE_PATTERN.fullmatch(
            str(self.failure_code)
        ):
            raise ValueError("failure_code deve usar somente A-Z, 0-9 e underscore.")
        if self.failure_message is not None:
            message = str(self.failure_message)
            if (
                "\n" in message
                or "\r" in message
                or len(message) > 500
                or _SENSITIVE_FAILURE_PATTERN.search(message)
            ):
                raise ValueError(
                    "failure_message deve ser sanitizada, ter uma linha e no máximo 500 caracteres."
                )
        if self.checkpoint_path is not None:
            checkpoint = Path(str(self.checkpoint_path))
            if not checkpoint.is_absolute() or ".." in checkpoint.parts:
                raise ValueError(
                    "checkpoint_path deve ser absoluto e não pode conter '..'."
                )
        if not isinstance(self.artifact_references, Mapping):
            raise ValueError("artifact_references deve ser um objeto.")

        detached_references = _freeze_json(dict(self.artifact_references))
        object.__setattr__(self, "client_id", client_id)
        object.__setattr__(self, "source_run_id", source_run_id)
        object.__setattr__(self, "artifact_references", detached_references)


@dataclass(frozen=True, slots=True)
class ComponentSetSummary:
    status: ComponentSetStatus
    available_components: tuple[ReportComponent, ...]
    retryable_components: tuple[ReportComponent, ...]
    missing_components: tuple[ReportComponent, ...]
    artifact_references_by_component: Mapping[
        ReportComponent,
        Mapping[str, Any],
    ]


def _latest_attempts(
    attempts: Sequence[ComponentAttempt],
) -> dict[ReportComponent, ComponentAttempt]:
    latest: dict[ReportComponent, ComponentAttempt] = {}
    for attempt in attempts:
        current = latest.get(attempt.component)
        if current is None or attempt.attempt_number > current.attempt_number:
            latest[attempt.component] = attempt
    return latest


def retryable_components(
    attempts: Sequence[ComponentAttempt],
) -> tuple[ReportComponent, ...]:
    latest = _latest_attempts(attempts)
    return tuple(
        component
        for component in ReportComponent
        if (
            component in latest
            and latest[component].status in _FAILED_STATUSES
            and latest[component].retryable
        )
    )


def validate_component_selection(
    selected_components: Sequence[ReportComponent],
    latest_attempts: Sequence[ComponentAttempt],
    *,
    vm_checkpoint_available: bool,
) -> str | None:
    del latest_attempts
    selected = frozenset(selected_components)
    if (
        ReportComponent.WAS in selected
        and ReportComponent.VM_CORE not in selected
        and not vm_checkpoint_available
    ):
        return "MISSING_VM_CHECKPOINT_FOR_WAS"
    return None


def summarize_component_set(
    attempts: Sequence[ComponentAttempt],
    *,
    planned_components: Sequence[ReportComponent] = tuple(ReportComponent),
) -> ComponentSetSummary:
    planned_set = frozenset(planned_components)
    planned = tuple(
        component for component in ReportComponent if component in planned_set
    )
    all_latest = _latest_attempts(attempts)
    latest = {
        component: all_latest[component]
        for component in planned
        if component in all_latest
    }
    missing = tuple(component for component in planned if component not in latest)
    available = tuple(
        component
        for component in planned
        if component in latest and latest[component].status in _AVAILABLE_STATUSES
    )
    failed = tuple(
        component
        for component in planned
        if component in latest and latest[component].status in _FAILED_STATUSES
    )

    if not latest or any(
        attempt.status in _PENDING_STATUSES for attempt in latest.values()
    ):
        status = ComponentSetStatus.PENDING
    elif available and (failed or missing):
        status = ComponentSetStatus.PARTIAL_FAILURE
    elif available and not failed and not missing:
        status = ComponentSetStatus.COMPLETE
    else:
        status = ComponentSetStatus.FAILED

    references = {
        component: _freeze_json(dict(latest[component].artifact_references))
        for component in available
    }
    return ComponentSetSummary(
        status=status,
        available_components=available,
        retryable_components=retryable_components(tuple(latest.values())),
        missing_components=missing,
        artifact_references_by_component=_freeze_json(references),
    )


__all__ = [
    "ComponentAttempt",
    "ComponentSetStatus",
    "ComponentSetSummary",
    "ComponentStage",
    "ComponentStatus",
    "ReportComponent",
    "retryable_components",
    "summarize_component_set",
    "validate_component_selection",
]
