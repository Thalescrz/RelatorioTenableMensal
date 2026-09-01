from __future__ import annotations

import json
from uuid import UUID

import pytest

from tenable_reports.application.web_batches import build_manual_batch_options
from tenable_reports.domain.web_batches import (
    BatchJobStatus,
    BatchStatus,
    InvalidBatchTransitionError,
    InvalidBatchJobTransitionError,
    WebBatchJob,
    retryable_batch_job_ids,
    transition_batch,
    transition_batch_job,
)


def _manual_batch_clients() -> list[dict[str, object]]:
    return [
        {
            "client_id": "a",
            "enabled": True,
            "responsible_analyst_id": "ana-1",
            "responsible_analyst_name": "Analista Um",
            "responsible_analyst_active": True,
        },
        {
            "client_id": "b",
            "enabled": True,
            "responsible_analyst_id": "ana-2",
            "responsible_analyst_name": "Analista Dois",
            "responsible_analyst_active": True,
        },
        {
            "client_id": "c",
            "enabled": True,
            "responsible_analyst_id": None,
            "responsible_analyst_name": None,
            "responsible_analyst_active": False,
        },
        {
            "client_id": "d",
            "enabled": False,
            "responsible_analyst_id": None,
            "responsible_analyst_name": None,
            "responsible_analyst_active": False,
        },
    ]


def test_manual_batch_options_persist_exact_selection_exclusions_and_analysts() -> None:
    options = build_manual_batch_options(
        clients=_manual_batch_clients(),
        selected_client_ids=["a", "c"],
        selection_filter_snapshot={"analyst_id": "ana-1", "query": ""},
    )

    assert options["selected_client_ids"] == ["a", "c"]
    assert options["excluded_client_ids"] == ["b"]
    assert options["selection_filter_snapshot"] == {
        "analyst_id": "ana-1",
        "query": "",
    }
    assert options["analyst_snapshot_by_client"] == {
        "a": {
            "analyst_id": "ana-1",
            "display_name": "Analista Um",
            "active": True,
        },
        "c": {
            "analyst_id": None,
            "display_name": None,
            "active": False,
        },
    }


def test_manual_batch_options_reject_empty_selection() -> None:
    with pytest.raises(ValueError, match="EMPTY_CLIENT_SELECTION"):
        build_manual_batch_options(
            clients=_manual_batch_clients(),
            selected_client_ids=[],
            selection_filter_snapshot={"analyst_id": None, "query": ""},
        )


@pytest.mark.parametrize("selected_client_id", ("unknown", "d"))
def test_manual_batch_options_reject_unknown_or_inactive_client(
    selected_client_id: str,
) -> None:
    with pytest.raises(ValueError, match="UNKNOWN_CLIENT_SELECTION"):
        build_manual_batch_options(
            clients=_manual_batch_clients(),
            selected_client_ids=[selected_client_id],
            selection_filter_snapshot={"analyst_id": None, "query": ""},
        )


def test_manual_batch_options_reject_duplicate_selection() -> None:
    with pytest.raises(ValueError, match="DUPLICATE_CLIENT_SELECTION"):
        build_manual_batch_options(
            clients=_manual_batch_clients(),
            selected_client_ids=["a", "a"],
            selection_filter_snapshot={"analyst_id": None, "query": ""},
        )


@pytest.mark.parametrize("invalid_filter", ("", [], 0, False))
def test_manual_batch_options_reject_falsy_invalid_filter_type(
    invalid_filter: object,
) -> None:
    with pytest.raises(ValueError, match="INVALID_SELECTION_FILTER"):
        build_manual_batch_options(
            clients=_manual_batch_clients(),
            selected_client_ids=["a"],
            selection_filter_snapshot=invalid_filter,
        )


def test_manual_batch_options_are_detached_from_mutable_inputs() -> None:
    clients = _manual_batch_clients()
    selection_filter = {"analyst_id": "ana-1", "query": ""}
    options = build_manual_batch_options(
        clients=clients,
        selected_client_ids=["a", "c"],
        selection_filter_snapshot=selection_filter,
    )
    serialized_before = json.dumps(options, ensure_ascii=False, sort_keys=True)

    clients[0]["responsible_analyst_name"] = "Nome alterado"
    clients[2]["responsible_analyst_id"] = "ana-alterada"
    clients.append({"client_id": "novo", "enabled": True})
    selection_filter["analyst_id"] = "ana-alterada"
    selection_filter["query"] = "consulta alterada"

    assert json.dumps(options, ensure_ascii=False, sort_keys=True) == serialized_before


@pytest.mark.parametrize(
    ("current", "requested"),
    (
        (BatchStatus.QUEUED, BatchStatus.RUNNING),
        (BatchStatus.RUNNING, BatchStatus.PAUSE_REQUESTED),
        (BatchStatus.PAUSE_REQUESTED, BatchStatus.PAUSED),
        (BatchStatus.PAUSED, BatchStatus.RUNNING),
        (BatchStatus.RUNNING, BatchStatus.STOP_REQUESTED),
        (BatchStatus.STOP_REQUESTED, BatchStatus.STOPPED),
        (BatchStatus.RUNNING, BatchStatus.COMPLETE),
        (BatchStatus.RUNNING, BatchStatus.COMPLETE_WITH_FAILURES),
        (BatchStatus.RUNNING, BatchStatus.COMPLETE_WITH_WARNINGS),
    ),
)
def test_batch_transition_accepts_the_approved_lifecycle(
    current: BatchStatus,
    requested: BatchStatus,
) -> None:
    assert transition_batch(current, requested) is requested


@pytest.mark.parametrize(
    "terminal",
    (
        BatchStatus.STOPPED,
        BatchStatus.COMPLETE,
        BatchStatus.COMPLETE_WITH_FAILURES,
        BatchStatus.COMPLETE_WITH_WARNINGS,
    ),
)
def test_batch_transition_rejects_leaving_a_terminal_state(
    terminal: BatchStatus,
) -> None:
    with pytest.raises(InvalidBatchTransitionError) as captured:
        transition_batch(terminal, BatchStatus.RUNNING)

    assert captured.value.current is terminal
    assert captured.value.requested is BatchStatus.RUNNING


@pytest.mark.parametrize(
    ("current", "requested"),
    (
        (BatchJobStatus.QUEUED, BatchJobStatus.RUNNING),
        (BatchJobStatus.QUEUED, BatchJobStatus.CANCELLED_BY_USER),
        (BatchJobStatus.RUNNING, BatchJobStatus.WAITING_WAS_DECISION),
        (BatchJobStatus.WAITING_WAS_DECISION, BatchJobStatus.RUNNING),
        (BatchJobStatus.RUNNING, BatchJobStatus.INTERRUPT_REQUESTED),
        (BatchJobStatus.INTERRUPT_REQUESTED, BatchJobStatus.INTERRUPTED),
        (BatchJobStatus.RUNNING, BatchJobStatus.COMPLETE),
        (BatchJobStatus.RUNNING, BatchJobStatus.COMPLETE_WITH_WARNINGS),
        (BatchJobStatus.RUNNING, BatchJobStatus.FAILED),
    ),
)
def test_batch_job_transition_accepts_the_approved_lifecycle(
    current: BatchJobStatus,
    requested: BatchJobStatus,
) -> None:
    assert transition_batch_job(current, requested) is requested


def test_batch_job_transition_rejects_requeueing_an_interrupted_job() -> None:
    with pytest.raises(InvalidBatchJobTransitionError):
        transition_batch_job(BatchJobStatus.INTERRUPTED, BatchJobStatus.QUEUED)


def test_retry_selection_contains_only_failed_interrupted_and_cancelled_jobs() -> None:
    jobs = (
        _job(position=1, status=BatchJobStatus.COMPLETE),
        _job(position=2, status=BatchJobStatus.FAILED),
        _job(position=3, status=BatchJobStatus.INTERRUPTED),
        _job(position=4, status=BatchJobStatus.CANCELLED_BY_USER),
        _job(position=5, status=BatchJobStatus.COMPLETE_WITH_WARNINGS),
    )

    assert retryable_batch_job_ids(jobs) == (
        UUID(int=2),
        UUID(int=3),
        UUID(int=4),
    )


def _job(*, position: int, status: BatchJobStatus) -> WebBatchJob:
    return WebBatchJob(
        id=UUID(int=position),
        batch_id=UUID(int=100),
        client_id=f"client-{position}",
        position=position,
        status=status,
        attempt_number=1,
    )
