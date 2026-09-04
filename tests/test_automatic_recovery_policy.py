from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from tenable_reports.application.automatic_recovery import (
    AutomaticRecoveryPolicy,
    RecoveryAction,
    decide_recovery,
)
from tenable_reports.domain.remote_components import (
    RemoteComponentState,
    RemoteComponentWindow,
    RemoteIdentifierKind,
    RemoteObservation,
)
from tenable_reports.domain.report_components import ReportComponent


JOB_ID = UUID("00000000-0000-0000-0000-000000000010")
COMPONENT_ID = UUID("00000000-0000-0000-0000-000000000020")
UUID_A = "00000000-0000-0000-0000-000000000111"


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def component_window(
    *,
    state: RemoteComponentState = RemoteComponentState.RUNNING_WINDOW_1,
    window_number: int = 1,
    deadline_at: datetime | None = None,
    remote_identifier: str | None = UUID_A,
    replacement_created_in_window_2: bool = False,
    replacement_created_in_window_3: bool = False,
) -> RemoteComponentWindow:
    return RemoteComponentWindow(
        id=COMPONENT_ID,
        batch_job_id=JOB_ID,
        component=ReportComponent.VM_CORE,
        state=state,
        window_number=window_number,
        attempt_number=window_number,
        origin="AUTOMATIC_MONTHLY",
        deadline_at=deadline_at or dt("2026-09-04T20:00:00Z"),
        identifier_kind=(
            RemoteIdentifierKind.UUID if remote_identifier is not None else None
        ),
        remote_identifier=remote_identifier,
        replacement_created_in_window_2=replacement_created_in_window_2,
        replacement_created_in_window_3=replacement_created_in_window_3,
    )


def test_window_two_reuses_a_valid_processing_uuid() -> None:
    decision = decide_recovery(
        component_window(
            state=RemoteComponentState.RUNNING_WINDOW_2,
            window_number=2,
        ),
        RemoteObservation.processing(completed=1, total=3),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert decision.action is RecoveryAction.CONTINUE_CURRENT
    assert decision.next_window is None


def test_window_two_invalid_uuid_creates_one_replacement_and_unlocks_window_three() -> None:
    decision = decide_recovery(
        component_window(
            state=RemoteComponentState.RUNNING_WINDOW_2,
            window_number=2,
        ),
        RemoteObservation.invalid_identifier(code="REMOTE_IDENTIFIER_NOT_FOUND"),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert decision.action is RecoveryAction.CREATE_REPLACEMENT
    assert decision.mark_replacement_in_window_two is True
    assert decision.next_window is None


def test_window_three_timeout_never_creates_window_four() -> None:
    decision = decide_recovery(
        component_window(
            state=RemoteComponentState.RUNNING_WINDOW_3,
            window_number=3,
            deadline_at=dt("2026-09-04T09:59:59Z"),
            replacement_created_in_window_2=True,
        ),
        RemoteObservation.processing(completed=2, total=4),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert decision.action is RecoveryAction.WAIT_MANUAL_RETRY
    assert decision.next_window is None
    assert decision.failure_code == "AUTOMATIC_RETRY_EXHAUSTED"


def test_success_and_valid_empty_result_complete_without_retry() -> None:
    for observation in (
        RemoteObservation.complete(completed=3, total=3),
        RemoteObservation.complete(completed=0, total=0, valid_empty=True),
    ):
        decision = decide_recovery(
            component_window(),
            observation,
            now=dt("2026-09-04T10:00:00Z"),
        )
        assert decision.action is RecoveryAction.COMPLETE
        assert decision.next_window is None


def test_not_contracted_component_is_marked_not_applicable() -> None:
    decision = decide_recovery(
        component_window(),
        RemoteObservation.not_applicable(code="MODULE_NOT_CONTRACTED"),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert decision.action is RecoveryAction.MARK_NOT_APPLICABLE


def test_terminal_retryable_failure_in_window_one_starts_window_two_early() -> None:
    decision = decide_recovery(
        component_window(),
        RemoteObservation.terminal_retryable_failure(code="REMOTE_EXPORT_FAILED"),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert decision.action is RecoveryAction.START_NEXT_WINDOW
    assert decision.next_window == 2


def test_window_two_timeout_without_replacement_waits_for_manual_retry() -> None:
    decision = decide_recovery(
        component_window(
            state=RemoteComponentState.RUNNING_WINDOW_2,
            window_number=2,
            deadline_at=dt("2026-09-04T09:59:59Z"),
        ),
        RemoteObservation.processing(completed=1, total=3),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert decision.action is RecoveryAction.WAIT_MANUAL_RETRY
    assert decision.next_window is None


def test_window_two_timeout_after_replacement_starts_window_three() -> None:
    decision = decide_recovery(
        component_window(
            state=RemoteComponentState.RUNNING_WINDOW_2,
            window_number=2,
            deadline_at=dt("2026-09-04T09:59:59Z"),
            replacement_created_in_window_2=True,
        ),
        RemoteObservation.processing(completed=1, total=3),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert decision.action is RecoveryAction.START_NEXT_WINDOW
    assert decision.next_window == 3


@pytest.mark.parametrize(
    "code",
    (
        "HTTP_401",
        "HTTP_403",
        "INVALID_PROFILE",
        "INVALID_QUERY",
    ),
)
def test_auth_permission_profile_and_query_fail_without_automatic_retry(
    code: str,
) -> None:
    decision = decide_recovery(
        component_window(),
        RemoteObservation.non_retryable_failure(code=code),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert decision.action is RecoveryAction.FAIL_NON_RETRYABLE
    assert decision.failure_code == code


@pytest.mark.parametrize("code", ("HTTP_429", "HTTP_500", "NETWORK_ERROR"))
def test_transient_errors_keep_the_same_identifier_inside_the_window(
    code: str,
) -> None:
    decision = decide_recovery(
        component_window(
            state=RemoteComponentState.RUNNING_WINDOW_2,
            window_number=2,
        ),
        RemoteObservation.transient_failure(code=code),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert decision.action is RecoveryAction.CONTINUE_CURRENT
    assert decision.next_window is None


def test_restart_does_not_change_the_persisted_deadline() -> None:
    deadline = dt("2026-09-04T20:00:00Z")
    window = component_window(deadline_at=deadline)

    decision = decide_recovery(
        window,
        RemoteObservation.processing(completed=0, total=None),
        now=deadline - timedelta(seconds=1),
    )

    assert decision.action is RecoveryAction.CONTINUE_CURRENT
    assert decision.deadline_at == deadline


def test_window_three_allows_only_one_last_replacement_inside_its_deadline() -> None:
    initial = component_window(
        state=RemoteComponentState.RUNNING_WINDOW_3,
        window_number=3,
        replacement_created_in_window_2=True,
    )
    first = decide_recovery(
        initial,
        RemoteObservation.invalid_identifier(code="REMOTE_IDENTIFIER_NOT_FOUND"),
        now=dt("2026-09-04T10:00:00Z"),
    )
    exhausted = decide_recovery(
        component_window(
            state=RemoteComponentState.RUNNING_WINDOW_3,
            window_number=3,
            replacement_created_in_window_2=True,
            replacement_created_in_window_3=True,
        ),
        RemoteObservation.invalid_identifier(code="REMOTE_IDENTIFIER_NOT_FOUND"),
        now=dt("2026-09-04T10:00:00Z"),
    )

    assert first.action is RecoveryAction.CREATE_REPLACEMENT
    assert first.mark_replacement_in_window_three is True
    assert exhausted.action is RecoveryAction.WAIT_MANUAL_RETRY
    assert exhausted.next_window is None


@pytest.mark.parametrize(
    "changes",
    (
        {"window_number": 4},
        {"deadline_at": datetime(2026, 9, 4, 20, 0)},
        {"window_number": 1, "replacement_created_in_window_2": True},
        {"window_number": 2, "replacement_created_in_window_3": True},
    ),
)
def test_component_window_rejects_invalid_window_contract(
    changes: dict[str, object],
) -> None:
    arguments: dict[str, object] = {
        "state": RemoteComponentState.RUNNING_WINDOW_2,
        "window_number": 2,
    }
    arguments.update(changes)

    with pytest.raises(ValueError):
        component_window(**arguments)  # type: ignore[arg-type]


def test_component_window_requires_identifier_kind_for_identifier() -> None:
    with pytest.raises(ValueError, match="identifier_kind"):
        RemoteComponentWindow(
            id=COMPONENT_ID,
            batch_job_id=JOB_ID,
            component=ReportComponent.VM_CORE,
            state=RemoteComponentState.RUNNING_WINDOW_1,
            window_number=1,
            attempt_number=1,
            origin="AUTOMATIC_MONTHLY",
            deadline_at=datetime(2026, 9, 4, 20, 0, tzinfo=UTC),
            identifier_kind=None,
            remote_identifier=UUID_A,
        )


def test_policy_rejects_values_outside_the_approved_contract() -> None:
    with pytest.raises(ValueError):
        AutomaticRecoveryPolicy(automatic_window_seconds=1)
    with pytest.raises(ValueError):
        AutomaticRecoveryPolicy(automatic_base_windows=3)
    with pytest.raises(ValueError):
        AutomaticRecoveryPolicy(automatic_replacement_window=False)
    with pytest.raises(ValueError):
        AutomaticRecoveryPolicy(manual_retry_window_seconds=1)
