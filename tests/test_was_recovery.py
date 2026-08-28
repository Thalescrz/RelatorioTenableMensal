from __future__ import annotations

import json

import pytest

from tenable_reports.application.was_recovery import (
    WasFailureDetails,
    WasRecoveryCheckpoint,
    WasRecoveryDecision,
    load_was_recovery_checkpoint,
    write_was_recovery_checkpoint,
)


def _checkpoint(tmp_path) -> WasRecoveryCheckpoint:
    return WasRecoveryCheckpoint(
        schema_version=1,
        run_id="run-1",
        client_id="client-a",
        tenant_id="tenant-a",
        execution_type="MANUAL",
        period={
            "start_at": "2026-07-01T00:00:00-03:00",
            "end_at": "2026-08-01T00:00:00-03:00",
            "timezone": "America/Fortaleza",
            "mode": "MANUAL",
        },
        profile_path="clients/managed/client-a.json",
        output_root=str(tmp_path),
        include_output=False,
        was_status="UNAVAILABLE",
        was_failure=WasFailureDetails(
            code="WAS_COLLECTION_UNAVAILABLE",
            message="Coleta WEB indisponível.",
            retryable=True,
            export_uuid="was-job",
            origin="created",
            remote_status="PROCESSING",
            completed_chunks=0,
            total_chunks=1,
            timeout_phase="processing",
            progress_made=False,
            safe_cancel_available=True,
        ),
    )


def test_checkpoint_round_trip_preserves_context_without_credentials(tmp_path) -> None:
    checkpoint = _checkpoint(tmp_path)

    path = write_was_recovery_checkpoint(tmp_path / "checkpoint.json", checkpoint)

    assert load_was_recovery_checkpoint(path) == checkpoint
    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "access_key" not in serialized
    assert "secret_key" not in serialized
    assert "password" not in serialized
    assert "token" not in serialized


def test_checkpoint_rejects_wrong_client(tmp_path) -> None:
    path = write_was_recovery_checkpoint(
        tmp_path / "checkpoint.json", _checkpoint(tmp_path)
    )

    with pytest.raises(ValueError, match="checkpoint WAS incompatível"):
        load_was_recovery_checkpoint(path, client_id="client-b")


def test_checkpoint_rejects_unknown_schema(tmp_path) -> None:
    payload = _checkpoint(tmp_path).to_dict()
    payload["schema_version"] = 99
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="schema"):
        load_was_recovery_checkpoint(path)


def test_recovery_decisions_are_stable_external_values() -> None:
    assert WasRecoveryDecision.CONTINUE_WITHOUT_WAS.value == "continue_without_was"
    assert WasRecoveryDecision.RETRY_WAS.value == "retry_was"
