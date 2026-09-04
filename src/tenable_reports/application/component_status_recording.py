"""Build durable component states for an initially published report set."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from tenable_reports.domain.report_components import (
    ComponentAttempt,
    ComponentStage,
    ComponentStatus,
    ReportComponent,
)


_SUCCESS_STATUSES = frozenset({
    "AVAILABLE",
    "COMPLETE",
    "FINISHED",
    "NO_DATA",
    "REPLAYED",
})
_CODE_CLEANUP = re.compile(r"[^A-Z0-9_]+")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _failure_values(
    value: Mapping[str, Any] | None,
    *,
    fallback_code: str,
    fallback_message: str,
) -> tuple[str, str, bool]:
    payload = value if isinstance(value, Mapping) else {}
    raw_code = str(payload.get("code") or fallback_code).strip().upper()
    code = _CODE_CLEANUP.sub("_", raw_code).strip("_")[:100]
    if not code or not code[0].isalpha():
        code = fallback_code
    message = " ".join(
        str(payload.get("message") or fallback_message).splitlines()
    ).strip()[:500]
    return code, message or fallback_message, bool(payload.get("retryable"))


def _attempt_id(
    source_run_id: str,
    component: ReportComponent,
    attempt_number: int,
) -> Any:
    return uuid5(
        NAMESPACE_URL,
        f"tenable-report-component:{source_run_id}:{component.value}:{attempt_number}",
    )


def build_initial_component_attempts(
    *,
    client_id: str,
    source_run_id: str,
    vm_status: str = "COMPLETE",
    vm_failure: Mapping[str, Any] | None = None,
    was_enabled: bool,
    was_status: str,
    cloud_enabled: bool,
    cloud_status: str,
    cloud_warnings: Sequence[Mapping[str, Any]],
    artifact_references_by_component: Mapping[
        ReportComponent, Mapping[str, Any]
    ],
    was_failure: Mapping[str, Any] | None = None,
    checkpoint_path: str | None = None,
) -> tuple[ComponentAttempt, ...]:
    """Return one deterministic attempt for every report component."""

    created_at = _now()
    normalized_vm = str(vm_status or "").strip().upper()
    if normalized_vm in _SUCCESS_STATUSES:
        vm_attempt = ComponentAttempt(
            id=_attempt_id(source_run_id, ReportComponent.VM_CORE, 1),
            client_id=client_id,
            source_run_id=source_run_id,
            component=ReportComponent.VM_CORE,
            status=ComponentStatus.COMPLETE,
            stage=ComponentStage.REPORT_PUBLICATION,
            attempt_number=1,
            checkpoint_path=checkpoint_path,
            artifact_references=artifact_references_by_component.get(
                ReportComponent.VM_CORE, {}
            ),
            created_at=created_at,
            ended_at=created_at,
        )
    else:
        code, message, retryable = _failure_values(
            vm_failure,
            fallback_code="VM_COMPONENT_FAILED",
            fallback_message="A coleta VM não foi concluída nesta execução.",
        )
        vm_attempt = ComponentAttempt(
            id=_attempt_id(source_run_id, ReportComponent.VM_CORE, 1),
            client_id=client_id,
            source_run_id=source_run_id,
            component=ReportComponent.VM_CORE,
            status=ComponentStatus.FAILED,
            stage=ComponentStage.COLLECTION,
            attempt_number=1,
            retryable=retryable,
            failure_code=code,
            failure_message=message,
            checkpoint_path=checkpoint_path,
            created_at=created_at,
            ended_at=created_at,
        )
    attempts: list[ComponentAttempt] = [vm_attempt]

    normalized_was = str(was_status or "").strip().upper()
    if not was_enabled:
        was_attempt = ComponentAttempt(
            id=_attempt_id(source_run_id, ReportComponent.WAS, 1),
            client_id=client_id,
            source_run_id=source_run_id,
            component=ReportComponent.WAS,
            status=ComponentStatus.SKIPPED,
            stage=ComponentStage.COLLECTION,
            attempt_number=1,
            created_at=created_at,
            ended_at=created_at,
        )
    elif normalized_was in _SUCCESS_STATUSES:
        was_attempt = ComponentAttempt(
            id=_attempt_id(source_run_id, ReportComponent.WAS, 1),
            client_id=client_id,
            source_run_id=source_run_id,
            component=ReportComponent.WAS,
            status=ComponentStatus.COMPLETE,
            stage=ComponentStage.REPORT_PUBLICATION,
            attempt_number=1,
            artifact_references=artifact_references_by_component.get(
                ReportComponent.WAS, {}
            ),
            created_at=created_at,
            ended_at=created_at,
        )
    else:
        code, message, retryable = _failure_values(
            was_failure,
            fallback_code="WAS_COMPONENT_FAILED",
            fallback_message="A coleta WAS não foi concluída nesta execução.",
        )
        was_attempt = ComponentAttempt(
            id=_attempt_id(source_run_id, ReportComponent.WAS, 1),
            client_id=client_id,
            source_run_id=source_run_id,
            component=ReportComponent.WAS,
            status=ComponentStatus.FAILED,
            stage=ComponentStage.COLLECTION,
            attempt_number=1,
            retryable=retryable,
            failure_code=code,
            failure_message=message,
            checkpoint_path=checkpoint_path,
            created_at=created_at,
            ended_at=created_at,
        )
    attempts.append(was_attempt)

    normalized_cloud = str(cloud_status or "").strip().upper()
    if not cloud_enabled or normalized_cloud == "DISABLED":
        cloud_attempt = ComponentAttempt(
            id=_attempt_id(source_run_id, ReportComponent.CLOUD, 1),
            client_id=client_id,
            source_run_id=source_run_id,
            component=ReportComponent.CLOUD,
            status=ComponentStatus.SKIPPED,
            stage=ComponentStage.COLLECTION,
            attempt_number=1,
            created_at=created_at,
            ended_at=created_at,
        )
    elif normalized_cloud in _SUCCESS_STATUSES:
        cloud_attempt = ComponentAttempt(
            id=_attempt_id(source_run_id, ReportComponent.CLOUD, 1),
            client_id=client_id,
            source_run_id=source_run_id,
            component=ReportComponent.CLOUD,
            status=(
                ComponentStatus.COMPLETE_WITH_WARNINGS
                if cloud_warnings
                else ComponentStatus.COMPLETE
            ),
            stage=ComponentStage.REPORT_PUBLICATION,
            attempt_number=1,
            artifact_references=artifact_references_by_component.get(
                ReportComponent.CLOUD, {}
            ),
            created_at=created_at,
            ended_at=created_at,
        )
    else:
        warning = next(
            (item for item in cloud_warnings if isinstance(item, Mapping)),
            None,
        )
        code, message, retryable = _failure_values(
            warning,
            fallback_code="CLOUD_COMPONENT_FAILED",
            fallback_message="O componente Cloud Security não foi concluído nesta execução.",
        )
        cloud_attempt = ComponentAttempt(
            id=_attempt_id(source_run_id, ReportComponent.CLOUD, 1),
            client_id=client_id,
            source_run_id=source_run_id,
            component=ReportComponent.CLOUD,
            status=ComponentStatus.FAILED,
            stage=ComponentStage.COLLECTION,
            attempt_number=1,
            retryable=retryable,
            failure_code=code,
            failure_message=message,
            created_at=created_at,
            ended_at=created_at,
        )
    attempts.append(cloud_attempt)
    return tuple(attempts)


def planned_components_from_attempts(
    attempts: Sequence[ComponentAttempt],
) -> tuple[ReportComponent, ...]:
    latest: dict[ReportComponent, ComponentAttempt] = {}
    for attempt in attempts:
        previous = latest.get(attempt.component)
        if previous is None or attempt.attempt_number > previous.attempt_number:
            latest[attempt.component] = attempt
    return tuple(
        component
        for component in ReportComponent
        if component in latest and latest[component].status is not ComponentStatus.SKIPPED
    )


__all__ = [
    "build_initial_component_attempts",
    "planned_components_from_attempts",
]
