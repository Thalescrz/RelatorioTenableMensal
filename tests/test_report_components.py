from __future__ import annotations

from copy import deepcopy
from uuid import UUID

import pytest

from tenable_reports.domain.report_components import (
    ComponentAttempt,
    ComponentSetStatus,
    ComponentStage,
    ComponentStatus,
    ReportComponent,
    retryable_components,
    summarize_component_set,
    validate_component_selection,
)


def _attempt(
    component: ReportComponent,
    status: ComponentStatus,
    *,
    attempt_number: int = 1,
    retryable: bool = False,
    artifact_references: dict[str, object] | None = None,
) -> ComponentAttempt:
    failure_code = (
        "COMPONENT_FAILED"
        if status in {ComponentStatus.FAILED, ComponentStatus.INTERRUPTED}
        else None
    )
    return ComponentAttempt(
        id=UUID(int=attempt_number),
        client_id="client-a",
        source_run_id="run-a",
        component=component,
        status=status,
        stage=ComponentStage.RENDER,
        attempt_number=attempt_number,
        retryable=retryable,
        failure_code=failure_code,
        artifact_references=artifact_references or {},
    )


def test_retryable_components_selects_only_failed_retryable_components() -> None:
    attempts = (
        _attempt(ReportComponent.VM_CORE, ComponentStatus.COMPLETE),
        _attempt(
            ReportComponent.WAS,
            ComponentStatus.FAILED,
            retryable=True,
        ),
        _attempt(
            ReportComponent.CLOUD,
            ComponentStatus.FAILED,
            retryable=True,
        ),
    )

    assert retryable_components(attempts) == (
        ReportComponent.WAS,
        ReportComponent.CLOUD,
    )


def test_retryable_components_uses_only_latest_attempt_per_component() -> None:
    attempts = (
        _attempt(
            ReportComponent.CLOUD,
            ComponentStatus.FAILED,
            attempt_number=1,
            retryable=True,
        ),
        _attempt(
            ReportComponent.CLOUD,
            ComponentStatus.COMPLETE,
            attempt_number=2,
        ),
    )

    assert retryable_components(attempts) == ()


def test_was_selection_requires_reusable_vm_checkpoint() -> None:
    latest_attempts = (
        _attempt(
            ReportComponent.WAS,
            ComponentStatus.FAILED,
            retryable=True,
        ),
    )

    assert validate_component_selection(
        (ReportComponent.WAS,),
        latest_attempts,
        vm_checkpoint_available=False,
    ) == "MISSING_VM_CHECKPOINT_FOR_WAS"
    assert validate_component_selection(
        (ReportComponent.VM_CORE, ReportComponent.WAS),
        latest_attempts,
        vm_checkpoint_available=False,
    ) is None


def test_component_set_summary_preserves_available_cloud_on_partial_failure() -> None:
    attempts = (
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

    summary = summarize_component_set(attempts)

    assert summary.status is ComponentSetStatus.PARTIAL_FAILURE
    assert summary.available_components == (ReportComponent.CLOUD,)
    assert summary.retryable_components == (
        ReportComponent.VM_CORE,
        ReportComponent.WAS,
    )
    assert summary.artifact_references_by_component[ReportComponent.CLOUD] == {
        "documents": ["cloud.docx"]
    }


def test_component_attempt_rejects_zero_attempt_number() -> None:
    with pytest.raises(ValueError, match="attempt_number"):
        _attempt(
            ReportComponent.CLOUD,
            ComponentStatus.FAILED,
            attempt_number=0,
            retryable=True,
        )


def test_complete_component_attempt_cannot_be_retryable() -> None:
    with pytest.raises(ValueError, match="retryable"):
        _attempt(
            ReportComponent.CLOUD,
            ComponentStatus.COMPLETE,
            retryable=True,
        )


def test_component_attempt_rejects_failure_message_longer_than_500() -> None:
    with pytest.raises(ValueError, match="failure_message"):
        ComponentAttempt(
            id=UUID(int=1),
            client_id="client-a",
            source_run_id="run-a",
            component=ReportComponent.CLOUD,
            status=ComponentStatus.FAILED,
            stage=ComponentStage.RENDER,
            attempt_number=1,
            retryable=True,
            failure_code="COMPONENT_FAILED",
            failure_message="x" * 501,
            artifact_references={},
        )


def test_component_attempt_detaches_mutable_artifact_references() -> None:
    references: dict[str, object] = {"documents": ["cloud.docx"]}
    expected = deepcopy(references)

    attempt = _attempt(
        ReportComponent.CLOUD,
        ComponentStatus.COMPLETE,
        artifact_references=references,
    )
    documents = references["documents"]
    assert isinstance(documents, list)
    documents.append("changed-after-creation.docx")

    assert attempt.artifact_references == expected


def test_component_attempt_artifact_references_are_deeply_immutable() -> None:
    attempt = _attempt(
        ReportComponent.CLOUD,
        ComponentStatus.COMPLETE,
        artifact_references={"documents": [{"path": "cloud.docx"}]},
    )

    documents = attempt.artifact_references["documents"]
    with pytest.raises((TypeError, AttributeError)):
        documents[0]["path"] = "mutated.docx"
    with pytest.raises((TypeError, AttributeError)):
        documents.append({"path": "other.docx"})

    assert attempt.artifact_references == {
        "documents": ({"path": "cloud.docx"},)
    }


def test_failed_component_attempt_requires_failure_code() -> None:
    with pytest.raises(ValueError, match="failure_code"):
        ComponentAttempt(
            id=UUID(int=1),
            client_id="client-a",
            source_run_id="run-a",
            component=ReportComponent.CLOUD,
            status=ComponentStatus.FAILED,
            stage=ComponentStage.RENDER,
            attempt_number=1,
            artifact_references={},
        )


@pytest.mark.parametrize(
    ("failure_code", "failure_message"),
    (
        ("COMPONENT_FAILED", None),
        (None, "Falha sanitizada."),
    ),
)
def test_complete_component_attempt_rejects_failure_details(
    failure_code: str | None,
    failure_message: str | None,
) -> None:
    with pytest.raises(ValueError, match="status|failure"):
        ComponentAttempt(
            id=UUID(int=1),
            client_id="client-a",
            source_run_id="run-a",
            component=ReportComponent.CLOUD,
            status=ComponentStatus.COMPLETE,
            stage=ComponentStage.REPORT_PUBLICATION,
            attempt_number=1,
            failure_code=failure_code,
            failure_message=failure_message,
            artifact_references={},
        )


@pytest.mark.parametrize(
    "failure_code",
    ("COMPONENT\nFAILED", "component_failed", "COMPONENT FAILED"),
)
def test_component_attempt_rejects_invalid_failure_code(
    failure_code: str,
) -> None:
    with pytest.raises(ValueError, match="failure_code"):
        ComponentAttempt(
            id=UUID(int=1),
            client_id="client-a",
            source_run_id="run-a",
            component=ReportComponent.CLOUD,
            status=ComponentStatus.FAILED,
            stage=ComponentStage.RENDER,
            attempt_number=1,
            failure_code=failure_code,
            artifact_references={},
        )


@pytest.mark.parametrize(
    "failure_message",
    ("token=fixture-value", "password: fixture-value"),
)
def test_component_attempt_rejects_sensitive_failure_message(
    failure_message: str,
) -> None:
    with pytest.raises(ValueError, match="failure_message"):
        ComponentAttempt(
            id=UUID(int=1),
            client_id="client-a",
            source_run_id="run-a",
            component=ReportComponent.CLOUD,
            status=ComponentStatus.FAILED,
            stage=ComponentStage.RENDER,
            attempt_number=1,
            failure_code="COMPONENT_FAILED",
            failure_message=failure_message,
            artifact_references={},
        )


def test_component_attempt_rejects_relative_checkpoint_path() -> None:
    with pytest.raises(ValueError, match="checkpoint_path"):
        ComponentAttempt(
            id=UUID(int=1),
            client_id="client-a",
            source_run_id="run-a",
            component=ReportComponent.CLOUD,
            status=ComponentStatus.FAILED,
            stage=ComponentStage.RENDER,
            attempt_number=1,
            failure_code="COMPONENT_FAILED",
            checkpoint_path="../outside.json",
            artifact_references={},
        )


def test_component_set_summary_marks_unreported_planned_components_missing() -> None:
    cloud = _attempt(
        ReportComponent.CLOUD,
        ComponentStatus.COMPLETE,
        artifact_references={"documents": ["cloud.docx"]},
    )

    summary = summarize_component_set((cloud,))

    assert summary.status is ComponentSetStatus.PARTIAL_FAILURE
    assert summary.available_components == (ReportComponent.CLOUD,)
    assert summary.missing_components == (
        ReportComponent.VM_CORE,
        ReportComponent.WAS,
    )


def test_component_set_summary_accepts_explicit_cloud_only_plan() -> None:
    cloud = _attempt(
        ReportComponent.CLOUD,
        ComponentStatus.COMPLETE,
        artifact_references={"documents": ["cloud.docx"]},
    )

    summary = summarize_component_set(
        (cloud,),
        planned_components=(ReportComponent.CLOUD,),
    )

    assert summary.status is ComponentSetStatus.COMPLETE
    assert summary.missing_components == ()


def test_component_set_summary_rejects_set_with_every_component_skipped() -> None:
    attempts = tuple(
        _attempt(component, ComponentStatus.SKIPPED)
        for component in ReportComponent
    )

    summary = summarize_component_set(attempts)

    assert summary.status is ComponentSetStatus.FAILED
    assert summary.available_components == ()
