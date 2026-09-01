"""Selective retry orchestration for independently published components."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import os
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from tenable_reports.application.report_components import ReportComponentRepository
from tenable_reports.domain.report_components import (
    ComponentAttempt,
    ComponentStage,
    ComponentStatus,
    ReportComponent,
    validate_component_selection,
)


class ComponentRetryStatus(StrEnum):
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    COMPONENT_NOT_RETRYABLE = "COMPONENT_NOT_RETRYABLE"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"


def _normalize_components(
    values: Sequence[ReportComponent],
    *,
    allow_empty: bool = False,
) -> tuple[ReportComponent, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("selected_components deve ser uma sequência.")
    try:
        selected = {ReportComponent(value) for value in values}
    except (TypeError, ValueError) as exc:
        raise ValueError("selected_components contém componente desconhecido.") from exc
    normalized = tuple(
        component for component in ReportComponent if component in selected
    )
    if not normalized and not allow_empty:
        raise ValueError("selected_components não pode ser vazio.")
    return normalized


def _copy_references(
    values: Mapping[ReportComponent, Mapping[str, Any]],
) -> Mapping[ReportComponent, Mapping[str, Any]]:
    if not isinstance(values, Mapping):
        raise ValueError("artifact_references_by_component deve ser um objeto.")
    copied: dict[ReportComponent, Mapping[str, Any]] = {}
    for raw_component, references in values.items():
        component = ReportComponent(raw_component)
        if not isinstance(references, Mapping):
            raise ValueError("Referências de artefatos devem ser objetos.")
        copied[component] = deepcopy(dict(references))
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class ComponentRetryRequest:
    source_run_id: str
    selected_components: Sequence[ReportComponent]
    failed_only: bool = True

    def __post_init__(self) -> None:
        source_run_id = str(self.source_run_id or "").strip()
        if not source_run_id:
            raise ValueError("source_run_id não pode ser vazio.")
        if not isinstance(self.failed_only, bool):
            raise ValueError("failed_only deve ser booleano.")
        object.__setattr__(self, "source_run_id", source_run_id)
        object.__setattr__(
            self,
            "selected_components",
            _normalize_components(self.selected_components),
        )


@dataclass(frozen=True, slots=True)
class ComponentRetrySource:
    source_run_id: str
    client_id: str
    manifest_path: Path
    vm_checkpoint_available: bool
    artifact_references_by_component: Mapping[
        ReportComponent,
        Mapping[str, Any],
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_run_id = str(self.source_run_id or "").strip()
        client_id = str(self.client_id or "").strip()
        if not source_run_id or not client_id:
            raise ValueError("source_run_id e client_id não podem ser vazios.")
        if not isinstance(self.vm_checkpoint_available, bool):
            raise ValueError("vm_checkpoint_available deve ser booleano.")
        manifest_path = Path(self.manifest_path).resolve()
        object.__setattr__(self, "source_run_id", source_run_id)
        object.__setattr__(self, "client_id", client_id)
        object.__setattr__(self, "manifest_path", manifest_path)
        object.__setattr__(
            self,
            "artifact_references_by_component",
            _copy_references(self.artifact_references_by_component),
        )


@dataclass(frozen=True, slots=True)
class ComponentRetryOutcome:
    status: ComponentRetryStatus
    completed_components: Sequence[ReportComponent] = ()
    artifact_references_by_component: Mapping[
        ReportComponent,
        Mapping[str, Any],
    ] = field(default_factory=dict)
    staged_paths: Sequence[Path] = ()
    failure_code: str | None = None
    failure_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ComponentRetryStatus(self.status))
        object.__setattr__(
            self,
            "completed_components",
            _normalize_components(self.completed_components, allow_empty=True),
        )
        object.__setattr__(
            self,
            "artifact_references_by_component",
            _copy_references(self.artifact_references_by_component),
        )
        if isinstance(self.staged_paths, (str, bytes)) or not isinstance(
            self.staged_paths,
            Sequence,
        ):
            raise ValueError("staged_paths deve ser uma sequência de caminhos.")
        object.__setattr__(
            self,
            "staged_paths",
            tuple(Path(path) for path in self.staged_paths),
        )


ComponentHandler = Callable[
    [ComponentRetrySource, Path, int],
    ComponentRetryOutcome,
]
ComponentPublisher = Callable[
    [ComponentRetrySource, Mapping[ReportComponent, ComponentRetryOutcome]],
    None,
]


@dataclass(frozen=True, slots=True)
class ComponentRetryDependencies:
    repository: ReportComponentRepository
    source_loader: Callable[[str], ComponentRetrySource]
    handlers: Mapping[ReportComponent, ComponentHandler]
    publisher: ComponentPublisher
    staging_root: Path

    def __post_init__(self) -> None:
        handlers = {
            ReportComponent(component): handler
            for component, handler in self.handlers.items()
        }
        object.__setattr__(self, "handlers", MappingProxyType(handlers))
        object.__setattr__(self, "staging_root", Path(self.staging_root).resolve())


def _latest_by_component(
    attempts: Sequence[ComponentAttempt],
) -> dict[ReportComponent, ComponentAttempt]:
    latest: dict[ReportComponent, ComponentAttempt] = {}
    for attempt in attempts:
        current = latest.get(attempt.component)
        if current is None or attempt.attempt_number > current.attempt_number:
            latest[attempt.component] = attempt
    return latest


def _result(
    status: ComponentRetryStatus,
    source: ComponentRetrySource,
    *,
    completed_components: Sequence[ReportComponent] = (),
    references: Mapping[ReportComponent, Mapping[str, Any]] | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> ComponentRetryOutcome:
    return ComponentRetryOutcome(
        status=status,
        completed_components=completed_components,
        artifact_references_by_component=(
            source.artifact_references_by_component
            if references is None
            else references
        ),
        failure_code=failure_code,
        failure_message=failure_message,
    )


def _is_retryable(attempt: ComponentAttempt | None) -> bool:
    return bool(
        attempt is not None
        and attempt.status in {ComponentStatus.FAILED, ComponentStatus.INTERRUPTED}
        and attempt.retryable
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _validate_handler_outcome(
    component: ReportComponent,
    outcome: ComponentRetryOutcome,
    *,
    staging_directory: Path,
) -> None:
    if outcome.status is not ComponentRetryStatus.COMPLETE:
        raise ValueError("Handler de componente não terminou com sucesso.")
    if outcome.completed_components != (component,):
        raise ValueError("Handler deve declarar somente o próprio componente.")
    if tuple(outcome.artifact_references_by_component) != (component,):
        raise ValueError("Handler deve publicar somente o próprio componente.")
    staging_root = staging_directory.resolve()
    for staged_path in outcome.staged_paths:
        if not _inside(staged_path.resolve(), staging_root):
            raise ValueError("Handler retornou caminho fora do staging da tentativa.")


def _restore_manifest(path: Path, original_bytes: bytes) -> None:
    restore_path = path.with_name(f".{path.name}.{uuid4().hex}.restore")
    try:
        restore_path.write_bytes(original_bytes)
        os.replace(restore_path, path)
    finally:
        restore_path.unlink(missing_ok=True)


def _remove_operation_staging(
    operation_directory: Path,
    *,
    staging_root: Path,
    remove_root_when_empty: bool,
) -> None:
    resolved_operation = operation_directory.resolve()
    resolved_root = staging_root.resolve()
    if _inside(resolved_operation, resolved_root) and resolved_operation.exists():
        shutil.rmtree(resolved_operation)
    if remove_root_when_empty and resolved_root.exists():
        try:
            resolved_root.rmdir()
        except OSError:
            pass


def _created_at() -> str:
    return datetime.now(UTC).isoformat()


def _persist_success(
    *,
    source: ComponentRetrySource,
    component: ReportComponent,
    attempt_number: int,
    outcome: ComponentRetryOutcome,
    repository: ReportComponentRepository,
) -> None:
    repository.create_attempt(
        ComponentAttempt(
            id=uuid4(),
            client_id=source.client_id,
            source_run_id=source.source_run_id,
            component=component,
            status=ComponentStatus.COMPLETE,
            stage=ComponentStage.REPORT_PUBLICATION,
            attempt_number=attempt_number,
            artifact_references=outcome.artifact_references_by_component[component],
            created_at=_created_at(),
            ended_at=_created_at(),
        )
    )


def _persist_failure(
    *,
    source: ComponentRetrySource,
    component: ReportComponent,
    attempt_number: int,
    repository: ReportComponentRepository,
) -> ComponentAttempt:
    message = f"Falha ao retentar o componente {component.value}."
    attempt = ComponentAttempt(
        id=uuid4(),
        client_id=source.client_id,
        source_run_id=source.source_run_id,
        component=component,
        status=ComponentStatus.FAILED,
        stage=ComponentStage.REPORT_PUBLICATION,
        attempt_number=attempt_number,
        retryable=True,
        failure_code=f"{component.value}_RETRY_FAILED",
        failure_message=message,
        artifact_references=source.artifact_references_by_component.get(
            component,
            {},
        ),
        created_at=_created_at(),
        ended_at=_created_at(),
    )
    return repository.create_attempt(attempt)


def retry_failed_components(
    request: ComponentRetryRequest,
    *,
    dependencies: ComponentRetryDependencies,
) -> ComponentRetryOutcome:
    source = dependencies.source_loader(request.source_run_id)
    if source.source_run_id != request.source_run_id:
        raise ValueError("A fonte não corresponde ao source_run_id solicitado.")

    latest_attempts = dependencies.repository.latest_attempts(
        source_run_id=source.source_run_id,
        client_id=source.client_id,
    )
    latest = _latest_by_component(latest_attempts)
    selected = tuple(request.selected_components)

    if request.failed_only and any(
        not _is_retryable(latest.get(component)) for component in selected
    ):
        return _result(ComponentRetryStatus.COMPONENT_NOT_RETRYABLE, source)

    dependency_error = validate_component_selection(
        selected,
        latest_attempts,
        vm_checkpoint_available=source.vm_checkpoint_available,
    )
    if dependency_error is not None:
        return _result(ComponentRetryStatus.MISSING_DEPENDENCY, source)

    manifest_path = source.manifest_path
    original_manifest = manifest_path.read_bytes()
    staging_root = dependencies.staging_root
    staging_root_preexisted = staging_root.exists()
    staging_root.mkdir(parents=True, exist_ok=True)
    operation_directory = staging_root / f"retry-{uuid4().hex}"
    operation_directory.mkdir(exist_ok=False)
    if not _inside(operation_directory.resolve(), staging_root.resolve()):
        raise ValueError("Diretório de staging fora da raiz configurada.")

    outcomes: dict[ReportComponent, ComponentRetryOutcome] = {}
    next_attempts = {
        component: (
            latest[component].attempt_number + 1 if component in latest else 1
        )
        for component in selected
    }
    current_component = selected[0]

    try:
        for component in selected:
            current_component = component
            handler = dependencies.handlers.get(component)
            if handler is None:
                raise ValueError("Handler não configurado para o componente.")
            outcome = handler(
                source,
                operation_directory,
                next_attempts[component],
            )
            _validate_handler_outcome(
                component,
                outcome,
                staging_directory=operation_directory,
            )
            outcomes[component] = outcome

        current_component = selected[-1]
        dependencies.publisher(source, MappingProxyType(outcomes))

        for component in selected:
            _persist_success(
                source=source,
                component=component,
                attempt_number=next_attempts[component],
                outcome=outcomes[component],
                repository=dependencies.repository,
            )

        merged_references = {
            component: deepcopy(dict(references))
            for component, references in source.artifact_references_by_component.items()
        }
        for component, outcome in outcomes.items():
            merged_references[component] = deepcopy(
                dict(outcome.artifact_references_by_component[component])
            )
        return _result(
            ComponentRetryStatus.COMPLETE,
            source,
            completed_components=selected,
            references=merged_references,
        )
    except Exception:
        _restore_manifest(manifest_path, original_manifest)
        _persist_failure(
            source=source,
            component=current_component,
            attempt_number=next_attempts[current_component],
            repository=dependencies.repository,
        )
        message = f"Falha ao retentar o componente {current_component.value}."
        return _result(
            ComponentRetryStatus.FAILED,
            source,
            failure_code=f"{current_component.value}_RETRY_FAILED",
            failure_message=message,
        )
    finally:
        _remove_operation_staging(
            operation_directory,
            staging_root=staging_root,
            remove_root_when_empty=not staging_root_preexisted,
        )


__all__ = [
    "ComponentRetryDependencies",
    "ComponentRetryOutcome",
    "ComponentRetryRequest",
    "ComponentRetrySource",
    "ComponentRetryStatus",
    "retry_failed_components",
]
