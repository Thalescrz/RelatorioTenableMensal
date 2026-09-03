from __future__ import annotations

from tenable_reports.application.component_status_recording import (
    build_initial_component_attempts,
    planned_components_from_attempts,
)
from tenable_reports.domain.report_components import (
    ComponentSetStatus,
    ComponentStatus,
    ReportComponent,
    summarize_component_set,
)


def test_initial_attempts_mark_enabled_failed_was_as_partial_and_retryable() -> None:
    attempts = build_initial_component_attempts(
        client_id="client-a",
        source_run_id="run-a",
        was_enabled=True,
        was_status="TIMED_OUT",
        was_failure={
            "code": "WAS_EXPORT_TIMEOUT",
            "message": "Tempo máximo excedido aguardando o export WAS.",
            "retryable": True,
        },
        cloud_enabled=True,
        cloud_status="COMPLETE",
        cloud_warnings=(),
        artifact_references_by_component={
            ReportComponent.VM_CORE: {"documents": ["base.docx"]},
            ReportComponent.CLOUD: {"documents": ["cloud.docx"]},
        },
    )

    by_component = {attempt.component: attempt for attempt in attempts}
    assert by_component[ReportComponent.VM_CORE].status is ComponentStatus.COMPLETE
    assert by_component[ReportComponent.WAS].status is ComponentStatus.FAILED
    assert by_component[ReportComponent.WAS].retryable is True
    assert by_component[ReportComponent.CLOUD].status is ComponentStatus.COMPLETE
    summary = summarize_component_set(
        attempts,
        planned_components=planned_components_from_attempts(attempts),
    )
    assert summary.status is ComponentSetStatus.PARTIAL_FAILURE
    assert summary.retryable_components == (ReportComponent.WAS,)


def test_disabled_optional_components_are_skipped_not_missing() -> None:
    attempts = build_initial_component_attempts(
        client_id="client-a",
        source_run_id="run-a",
        was_enabled=False,
        was_status="DISABLED",
        cloud_enabled=False,
        cloud_status="DISABLED",
        cloud_warnings=(),
        artifact_references_by_component={
            ReportComponent.VM_CORE: {"documents": ["base.docx"]},
        },
    )

    assert planned_components_from_attempts(attempts) == (
        ReportComponent.VM_CORE,
    )
    summary = summarize_component_set(
        attempts,
        planned_components=planned_components_from_attempts(attempts),
    )
    assert summary.status is ComponentSetStatus.COMPLETE
    assert summary.missing_components == ()


def test_successful_was_with_zero_findings_is_complete_not_partial() -> None:
    attempts = build_initial_component_attempts(
        client_id="cliente-sem-was-findings",
        source_run_id="run-sem-was-findings",
        was_enabled=True,
        was_status="NO_DATA",
        cloud_enabled=False,
        cloud_status="DISABLED",
        cloud_warnings=(),
        artifact_references_by_component={},
    )

    was = next(
        attempt for attempt in attempts if attempt.component is ReportComponent.WAS
    )
    summary = summarize_component_set(
        attempts,
        planned_components=planned_components_from_attempts(attempts),
    )

    assert was.status is ComponentStatus.COMPLETE
    assert summary.status is ComponentSetStatus.COMPLETE
