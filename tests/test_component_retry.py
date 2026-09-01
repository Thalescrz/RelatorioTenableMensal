from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping
from uuid import UUID

from tenable_reports.application.component_retry import (
    ComponentRetryDependencies,
    ComponentRetryOutcome,
    ComponentRetryRequest,
    ComponentRetrySource,
    ComponentRetryStatus,
    retry_failed_components,
)
from tenable_reports.domain.report_components import (
    ComponentAttempt,
    ComponentStage,
    ComponentStatus,
    ReportComponent,
)


class InMemoryComponentRepository:
    def __init__(self, attempts: tuple[ComponentAttempt, ...]) -> None:
        self.attempts = list(attempts)
        self.created: list[ComponentAttempt] = []

    def create_attempt(self, attempt: ComponentAttempt) -> ComponentAttempt:
        self.attempts.append(attempt)
        self.created.append(attempt)
        return attempt

    def latest_attempts(
        self,
        *,
        source_run_id: str,
        client_id: str,
    ) -> tuple[ComponentAttempt, ...]:
        latest: dict[ReportComponent, ComponentAttempt] = {}
        for attempt in self.attempts:
            if (
                attempt.source_run_id != source_run_id
                or attempt.client_id != client_id
            ):
                continue
            previous = latest.get(attempt.component)
            if previous is None or attempt.attempt_number > previous.attempt_number:
                latest[attempt.component] = attempt
        return tuple(
            latest[component]
            for component in ReportComponent
            if component in latest
        )


def _attempt(
    component: ReportComponent,
    status: ComponentStatus,
    *,
    attempt_number: int = 1,
    retryable: bool = False,
    artifact_references: Mapping[str, object] | None = None,
) -> ComponentAttempt:
    failure_code = None
    failure_message = None
    if status in {ComponentStatus.FAILED, ComponentStatus.INTERRUPTED}:
        failure_code = f"{component.value}_FAILED"
        failure_message = f"Falha sanitizada em {component.value}."
    return ComponentAttempt(
        id=UUID(int=(list(ReportComponent).index(component) + 1) * 100 + attempt_number),
        client_id="client-a",
        source_run_id="run-a",
        component=component,
        status=status,
        stage=ComponentStage.RENDER,
        attempt_number=attempt_number,
        retryable=retryable,
        failure_code=failure_code,
        failure_message=failure_message,
        artifact_references=artifact_references or {},
    )


def _source(
    tmp_path: Path,
    *,
    vm_checkpoint_available: bool = True,
) -> ComponentRetrySource:
    manifest_path = tmp_path / "publication-manifest.json"
    manifest_path.write_bytes(
        b'{"documents":["vm.docx","was.docx","cloud.docx"],"status":"PARTIAL"}'
    )
    return ComponentRetrySource(
        source_run_id="run-a",
        client_id="client-a",
        manifest_path=manifest_path,
        vm_checkpoint_available=vm_checkpoint_available,
        artifact_references_by_component={
            ReportComponent.VM_CORE: {"documents": ["vm.docx"]},
            ReportComponent.WAS: {"documents": ["was.docx"]},
            ReportComponent.CLOUD: {"documents": ["cloud.docx"]},
        },
    )


def _complete_handler(
    component: ReportComponent,
    calls: list[ReportComponent],
) -> Callable[[ComponentRetrySource, Path, int], ComponentRetryOutcome]:
    def handler(
        source: ComponentRetrySource,
        staging_directory: Path,
        attempt_number: int,
    ) -> ComponentRetryOutcome:
        del source, attempt_number
        calls.append(component)
        staging_directory.mkdir(parents=True, exist_ok=True)
        staged_path = staging_directory / f"{component.value.lower()}.docx"
        staged_path.write_bytes(b"fixture-document")
        return ComponentRetryOutcome(
            status=ComponentRetryStatus.COMPLETE,
            completed_components=(component,),
            artifact_references_by_component={
                component: {"documents": [f"{component.value.lower()}-new.docx"]}
            },
            staged_paths=(staged_path,),
        )

    return handler


def _unexpected_handler(
    component: ReportComponent,
) -> Callable[[ComponentRetrySource, Path, int], ComponentRetryOutcome]:
    def handler(
        source: ComponentRetrySource,
        staging_directory: Path,
        attempt_number: int,
    ) -> ComponentRetryOutcome:
        del source, staging_directory, attempt_number
        raise AssertionError(f"Handler inesperado para {component.value}.")

    return handler


def _dependencies(
    tmp_path: Path,
    *,
    source: ComponentRetrySource,
    repository: InMemoryComponentRepository,
    handlers: Mapping[
        ReportComponent,
        Callable[[ComponentRetrySource, Path, int], ComponentRetryOutcome],
    ],
    publisher: Callable[
        [ComponentRetrySource, Mapping[ReportComponent, ComponentRetryOutcome]],
        None,
    ],
) -> ComponentRetryDependencies:
    return ComponentRetryDependencies(
        repository=repository,
        source_loader=lambda source_run_id: (
            source
            if source_run_id == "run-a"
            else (_ for _ in ()).throw(KeyError(source_run_id))
        ),
        handlers=handlers,
        publisher=publisher,
        staging_root=tmp_path / "retry-staging",
    )


def test_retry_runs_only_selected_failed_cloud_and_persists_next_attempt(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    repository = InMemoryComponentRepository(
        (
            _attempt(
                ReportComponent.VM_CORE,
                ComponentStatus.COMPLETE,
                artifact_references={"documents": ["vm.docx"]},
            ),
            _attempt(
                ReportComponent.WAS,
                ComponentStatus.COMPLETE,
                artifact_references={"documents": ["was.docx"]},
            ),
            _attempt(
                ReportComponent.CLOUD,
                ComponentStatus.FAILED,
                retryable=True,
            ),
        )
    )
    handler_calls: list[ReportComponent] = []
    published_components: list[tuple[ReportComponent, ...]] = []
    dependencies = _dependencies(
        tmp_path,
        source=source,
        repository=repository,
        handlers={
            ReportComponent.VM_CORE: _unexpected_handler(ReportComponent.VM_CORE),
            ReportComponent.WAS: _unexpected_handler(ReportComponent.WAS),
            ReportComponent.CLOUD: _complete_handler(
                ReportComponent.CLOUD, handler_calls
            ),
        },
        publisher=lambda _source, outcomes: published_components.append(
            tuple(outcomes)
        ),
    )

    result = retry_failed_components(
        ComponentRetryRequest(
            source_run_id="run-a",
            selected_components=(ReportComponent.CLOUD,),
            failed_only=True,
        ),
        dependencies=dependencies,
    )

    assert result.status is ComponentRetryStatus.COMPLETE
    assert handler_calls == [ReportComponent.CLOUD]
    assert published_components == [(ReportComponent.CLOUD,)]
    assert len(repository.created) == 1
    persisted = repository.created[0]
    assert persisted.component is ReportComponent.CLOUD
    assert persisted.status is ComponentStatus.COMPLETE
    assert persisted.attempt_number == 2


def test_failed_only_rejects_complete_component_without_side_effects(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    repository = InMemoryComponentRepository(
        (_attempt(ReportComponent.CLOUD, ComponentStatus.COMPLETE),)
    )
    publisher_calls: list[str] = []
    dependencies = _dependencies(
        tmp_path,
        source=source,
        repository=repository,
        handlers={
            ReportComponent.CLOUD: _unexpected_handler(ReportComponent.CLOUD)
        },
        publisher=lambda _source, _outcomes: publisher_calls.append("published"),
    )

    result = retry_failed_components(
        ComponentRetryRequest(
            source_run_id="run-a",
            selected_components=(ReportComponent.CLOUD,),
            failed_only=True,
        ),
        dependencies=dependencies,
    )

    assert result.status is ComponentRetryStatus.COMPONENT_NOT_RETRYABLE
    assert repository.created == []
    assert publisher_calls == []


def test_was_retry_without_vm_checkpoint_returns_missing_dependency(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, vm_checkpoint_available=False)
    repository = InMemoryComponentRepository(
        (
            _attempt(
                ReportComponent.WAS,
                ComponentStatus.FAILED,
                retryable=True,
            ),
        )
    )
    publisher_calls: list[str] = []
    dependencies = _dependencies(
        tmp_path,
        source=source,
        repository=repository,
        handlers={ReportComponent.WAS: _unexpected_handler(ReportComponent.WAS)},
        publisher=lambda _source, _outcomes: publisher_calls.append("published"),
    )

    result = retry_failed_components(
        ComponentRetryRequest(
            source_run_id="run-a",
            selected_components=(ReportComponent.WAS,),
            failed_only=True,
        ),
        dependencies=dependencies,
    )

    assert result.status is ComponentRetryStatus.MISSING_DEPENDENCY
    assert repository.created == []
    assert publisher_calls == []


def test_retry_vm_and_was_preserves_completed_cloud_references(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, vm_checkpoint_available=False)
    repository = InMemoryComponentRepository(
        (
            _attempt(
                ReportComponent.VM_CORE,
                ComponentStatus.FAILED,
                retryable=True,
            ),
            _attempt(
                ReportComponent.WAS,
                ComponentStatus.FAILED,
                retryable=True,
            ),
            _attempt(
                ReportComponent.CLOUD,
                ComponentStatus.COMPLETE,
                artifact_references={"documents": ["cloud.docx"]},
            ),
        )
    )
    handler_calls: list[ReportComponent] = []
    published_components: list[tuple[ReportComponent, ...]] = []
    dependencies = _dependencies(
        tmp_path,
        source=source,
        repository=repository,
        handlers={
            ReportComponent.VM_CORE: _complete_handler(
                ReportComponent.VM_CORE, handler_calls
            ),
            ReportComponent.WAS: _complete_handler(
                ReportComponent.WAS, handler_calls
            ),
            ReportComponent.CLOUD: _unexpected_handler(ReportComponent.CLOUD),
        },
        publisher=lambda _source, outcomes: published_components.append(
            tuple(outcomes)
        ),
    )

    result = retry_failed_components(
        ComponentRetryRequest(
            source_run_id="run-a",
            selected_components=(ReportComponent.VM_CORE, ReportComponent.WAS),
            failed_only=True,
        ),
        dependencies=dependencies,
    )

    assert result.status is ComponentRetryStatus.COMPLETE
    assert handler_calls == [ReportComponent.VM_CORE, ReportComponent.WAS]
    assert published_components == [
        (ReportComponent.VM_CORE, ReportComponent.WAS)
    ]
    assert result.artifact_references_by_component[ReportComponent.CLOUD] == {
        "documents": ["cloud.docx"]
    }
    assert [attempt.component for attempt in repository.created] == [
        ReportComponent.VM_CORE,
        ReportComponent.WAS,
    ]


def test_handler_failure_preserves_manifest_cleans_staging_and_records_failure(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    original_manifest = source.manifest_path.read_bytes()
    repository = InMemoryComponentRepository(
        (
            _attempt(
                ReportComponent.CLOUD,
                ComponentStatus.FAILED,
                retryable=True,
            ),
        )
    )
    publisher_calls: list[str] = []

    def failing_handler(
        _source: ComponentRetrySource,
        staging_directory: Path,
        _attempt_number: int,
    ) -> ComponentRetryOutcome:
        staging_directory.mkdir(parents=True, exist_ok=True)
        (staging_directory / "partial.docx").write_bytes(b"partial")
        raise RuntimeError("token=fixture-sensitive")

    dependencies = _dependencies(
        tmp_path,
        source=source,
        repository=repository,
        handlers={ReportComponent.CLOUD: failing_handler},
        publisher=lambda _source, _outcomes: publisher_calls.append("published"),
    )

    result = retry_failed_components(
        ComponentRetryRequest(
            source_run_id="run-a",
            selected_components=(ReportComponent.CLOUD,),
            failed_only=True,
        ),
        dependencies=dependencies,
    )

    assert result.status is ComponentRetryStatus.FAILED
    assert source.manifest_path.read_bytes() == original_manifest
    assert not (tmp_path / "retry-staging").exists()
    assert publisher_calls == []
    assert len(repository.created) == 1
    failed_attempt = repository.created[0]
    assert failed_attempt.status is ComponentStatus.FAILED
    assert failed_attempt.retryable is True
    assert failed_attempt.failure_code == "CLOUD_RETRY_FAILED"
    assert failed_attempt.failure_message == "Falha ao retentar o componente CLOUD."
    assert "fixture-sensitive" not in failed_attempt.failure_message
    assert "token" not in failed_attempt.failure_message.lower()


def test_publisher_failure_restores_manifest_and_removes_new_staging(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    original_manifest = source.manifest_path.read_bytes()
    repository = InMemoryComponentRepository(
        (
            _attempt(
                ReportComponent.CLOUD,
                ComponentStatus.FAILED,
                retryable=True,
            ),
        )
    )
    handler_calls: list[ReportComponent] = []

    def failing_publisher(
        current_source: ComponentRetrySource,
        _outcomes: Mapping[ReportComponent, ComponentRetryOutcome],
    ) -> None:
        current_source.manifest_path.write_bytes(b"incomplete-publication")
        raise RuntimeError("token=fixture-sensitive")

    dependencies = _dependencies(
        tmp_path,
        source=source,
        repository=repository,
        handlers={
            ReportComponent.CLOUD: _complete_handler(
                ReportComponent.CLOUD, handler_calls
            )
        },
        publisher=failing_publisher,
    )

    result = retry_failed_components(
        ComponentRetryRequest(
            source_run_id="run-a",
            selected_components=(ReportComponent.CLOUD,),
            failed_only=True,
        ),
        dependencies=dependencies,
    )

    assert result.status is ComponentRetryStatus.FAILED
    assert handler_calls == [ReportComponent.CLOUD]
    assert source.manifest_path.read_bytes() == original_manifest
    assert not (tmp_path / "retry-staging").exists()
