from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from tenable_reports.application.publishing import write_json_atomic


CHECKPOINT_SCHEMA_VERSION = 1


class WasRecoveryDecision(StrEnum):
    CONTINUE_WITHOUT_WAS = "continue_without_was"
    RETRY_WAS = "retry_was"


@dataclass(frozen=True, slots=True)
class WasFailureDetails:
    code: str
    message: str = ""
    retryable: bool = False
    export_uuid: str | None = None
    origin: str | None = None
    remote_status: str | None = None
    completed_chunks: int = 0
    total_chunks: int = 0
    timeout_phase: str | None = None
    progress_made: bool = False
    safe_cancel_available: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WasFailureDetails:
        return cls(
            code=str(payload.get("code") or "").strip(),
            message=str(payload.get("message") or "").strip(),
            retryable=bool(payload.get("retryable", False)),
            export_uuid=_optional_text(payload.get("export_uuid")),
            origin=_optional_text(payload.get("origin")),
            remote_status=_optional_text(payload.get("remote_status")),
            completed_chunks=max(0, int(payload.get("completed_chunks") or 0)),
            total_chunks=max(0, int(payload.get("total_chunks") or 0)),
            timeout_phase=_optional_text(payload.get("timeout_phase")),
            progress_made=bool(payload.get("progress_made", False)),
            safe_cancel_available=bool(payload.get("safe_cancel_available", False)),
        )


@dataclass(frozen=True, slots=True)
class WasRecoveryCheckpoint:
    schema_version: int
    run_id: str
    client_id: str
    tenant_id: str
    execution_type: str
    period: Mapping[str, Any]
    profile_path: str
    output_root: str
    include_output: bool
    was_status: str
    was_failure: WasFailureDetails | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["period"] = dict(self.period)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WasRecoveryCheckpoint:
        schema_version = int(payload.get("schema_version") or 0)
        if schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"Versão de schema do checkpoint WAS incompatível: {schema_version}."
            )
        period = payload.get("period")
        if not isinstance(period, Mapping):
            raise ValueError("Período ausente no checkpoint WAS.")
        failure_payload = payload.get("was_failure")
        failure = (
            WasFailureDetails.from_dict(failure_payload)
            if isinstance(failure_payload, Mapping)
            else None
        )
        checkpoint = cls(
            schema_version=schema_version,
            run_id=_required_text(payload.get("run_id"), "run_id"),
            client_id=_required_text(payload.get("client_id"), "client_id"),
            tenant_id=_required_text(payload.get("tenant_id"), "tenant_id"),
            execution_type=_required_text(
                payload.get("execution_type"), "execution_type"
            ).upper(),
            period=dict(period),
            profile_path=_required_text(payload.get("profile_path"), "profile_path"),
            output_root=_required_text(payload.get("output_root"), "output_root"),
            include_output=bool(payload.get("include_output", False)),
            was_status=_required_text(payload.get("was_status"), "was_status").upper(),
            was_failure=failure,
        )
        _validate_checkpoint(checkpoint)
        return checkpoint


def write_was_recovery_checkpoint(
    path: str | Path,
    checkpoint: WasRecoveryCheckpoint,
) -> Path:
    _validate_checkpoint(checkpoint)
    return write_json_atomic(path, checkpoint.to_dict())


def load_was_recovery_checkpoint(
    path: str | Path,
    *,
    client_id: str | None = None,
    run_id: str | None = None,
) -> WasRecoveryCheckpoint:
    checkpoint_path = Path(path)
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Checkpoint WAS precisa ser um objeto JSON.")
    checkpoint = WasRecoveryCheckpoint.from_dict(payload)
    if client_id is not None and checkpoint.client_id != str(client_id).strip():
        raise ValueError("Cliente do checkpoint WAS incompatível com a solicitação.")
    if run_id is not None and checkpoint.run_id != str(run_id).strip():
        raise ValueError("Run do checkpoint WAS incompatível com a solicitação.")
    return checkpoint


def _validate_checkpoint(checkpoint: WasRecoveryCheckpoint) -> None:
    if checkpoint.schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(
            f"Versão de schema do checkpoint WAS incompatível: {checkpoint.schema_version}."
        )
    for value, field in (
        (checkpoint.run_id, "run_id"),
        (checkpoint.client_id, "client_id"),
        (checkpoint.tenant_id, "tenant_id"),
        (checkpoint.execution_type, "execution_type"),
        (checkpoint.profile_path, "profile_path"),
        (checkpoint.output_root, "output_root"),
        (checkpoint.was_status, "was_status"),
    ):
        _required_text(value, field)
    if not checkpoint.period.get("start_at") or not checkpoint.period.get("end_at"):
        raise ValueError("Período incompleto no checkpoint WAS.")


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Campo obrigatório ausente no checkpoint WAS: {field}.")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "WasFailureDetails",
    "WasRecoveryCheckpoint",
    "WasRecoveryDecision",
    "load_was_recovery_checkpoint",
    "write_was_recovery_checkpoint",
]
