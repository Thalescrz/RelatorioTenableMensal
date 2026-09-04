"""Pure decision policy for automatic remote-component recovery windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from tenable_reports.domain.remote_components import (
    RemoteComponentWindow,
    RemoteObservation,
    RemoteObservationKind,
)


class RecoveryAction(StrEnum):
    CONTINUE_CURRENT = "CONTINUE_CURRENT"
    COMPLETE = "COMPLETE"
    MARK_NOT_APPLICABLE = "MARK_NOT_APPLICABLE"
    START_NEXT_WINDOW = "START_NEXT_WINDOW"
    CREATE_REPLACEMENT = "CREATE_REPLACEMENT"
    WAIT_MANUAL_RETRY = "WAIT_MANUAL_RETRY"
    FAIL_NON_RETRYABLE = "FAIL_NON_RETRYABLE"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    deadline_at: datetime
    next_window: int | None = None
    mark_replacement_in_window_two: bool = False
    mark_replacement_in_window_three: bool = False
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class AutomaticRecoveryPolicy:
    automatic_window_seconds: int = 36_000
    automatic_base_windows: int = 2
    automatic_replacement_window: bool = True
    manual_retry_window_seconds: int = 36_000

    def __post_init__(self) -> None:
        if self.automatic_window_seconds != 36_000:
            raise ValueError("automatic_window_seconds deve ser 36000.")
        if self.automatic_base_windows != 2:
            raise ValueError("automatic_base_windows deve ser 2.")
        if self.automatic_replacement_window is not True:
            raise ValueError("automatic_replacement_window deve estar habilitada.")
        if self.manual_retry_window_seconds != 36_000:
            raise ValueError("manual_retry_window_seconds deve ser 36000.")


def _manual_retry(
    window: RemoteComponentWindow,
    *,
    failure_code: str = "AUTOMATIC_RETRY_EXHAUSTED",
) -> RecoveryDecision:
    return RecoveryDecision(
        action=RecoveryAction.WAIT_MANUAL_RETRY,
        deadline_at=window.deadline_at,
        failure_code=failure_code,
    )


def _next_after_exhaustion(
    window: RemoteComponentWindow,
) -> RecoveryDecision:
    if window.window_number == 1:
        return RecoveryDecision(
            action=RecoveryAction.START_NEXT_WINDOW,
            next_window=2,
            deadline_at=window.deadline_at,
        )
    if window.window_number == 2 and window.replacement_created_in_window_2:
        return RecoveryDecision(
            action=RecoveryAction.START_NEXT_WINDOW,
            next_window=3,
            deadline_at=window.deadline_at,
        )
    return _manual_retry(window)


def decide_recovery(
    window: RemoteComponentWindow,
    observation: RemoteObservation,
    *,
    now: datetime,
    policy: AutomaticRecoveryPolicy | None = None,
) -> RecoveryDecision:
    """Return the next durable action without mutating the persisted window."""

    policy = policy or AutomaticRecoveryPolicy()
    del policy
    if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
        raise ValueError("now deve usar timezone UTC.")

    if observation.kind is RemoteObservationKind.COMPLETE:
        return RecoveryDecision(
            action=RecoveryAction.COMPLETE,
            deadline_at=window.deadline_at,
        )
    if observation.kind is RemoteObservationKind.NOT_APPLICABLE:
        return RecoveryDecision(
            action=RecoveryAction.MARK_NOT_APPLICABLE,
            deadline_at=window.deadline_at,
        )
    if observation.kind is RemoteObservationKind.NON_RETRYABLE_FAILURE:
        return RecoveryDecision(
            action=RecoveryAction.FAIL_NON_RETRYABLE,
            deadline_at=window.deadline_at,
            failure_code=observation.failure_code,
        )

    if observation.kind is RemoteObservationKind.INVALID_IDENTIFIER:
        if window.window_number == 1:
            return RecoveryDecision(
                action=RecoveryAction.START_NEXT_WINDOW,
                next_window=2,
                deadline_at=window.deadline_at,
                failure_code=observation.failure_code,
            )
        if window.window_number == 2:
            if window.replacement_created_in_window_2:
                return RecoveryDecision(
                    action=RecoveryAction.START_NEXT_WINDOW,
                    next_window=3,
                    deadline_at=window.deadline_at,
                    failure_code=observation.failure_code,
                )
            return RecoveryDecision(
                action=RecoveryAction.CREATE_REPLACEMENT,
                deadline_at=window.deadline_at,
                mark_replacement_in_window_two=True,
                failure_code=observation.failure_code,
            )
        if (
            now >= window.deadline_at
            or window.replacement_created_in_window_3
        ):
            return _manual_retry(window)
        return RecoveryDecision(
            action=RecoveryAction.CREATE_REPLACEMENT,
            deadline_at=window.deadline_at,
            mark_replacement_in_window_three=True,
            failure_code=observation.failure_code,
        )

    if observation.kind is RemoteObservationKind.TERMINAL_RETRYABLE_FAILURE:
        return _next_after_exhaustion(window)

    if now >= window.deadline_at:
        return _next_after_exhaustion(window)

    return RecoveryDecision(
        action=RecoveryAction.CONTINUE_CURRENT,
        deadline_at=window.deadline_at,
    )


__all__ = [
    "AutomaticRecoveryPolicy",
    "RecoveryAction",
    "RecoveryDecision",
    "decide_recovery",
]
