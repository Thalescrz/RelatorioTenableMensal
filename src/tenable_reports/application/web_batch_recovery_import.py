"""Validate and import one legacy web-batch recovery snapshot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from tenable_reports.application.web_batches import assert_sanitized_payload
from tenable_reports.domain.web_batches import (
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchEvent,
    WebBatchJob,
)


_SNAPSHOT_SCHEMA_VERSION = 1
_STATUS_MAP = {
    "complete": BatchJobStatus.COMPLETE,
    "failed": BatchJobStatus.FAILED,
    "running": BatchJobStatus.INTERRUPTED,
    "queued": BatchJobStatus.QUEUED,
}


class WebBatchRecoveryRepository(Protocol):
    def get_batch(self, batch_id: UUID) -> WebBatch | None: ...

    def import_recovery_batch(
        self,
        batch: WebBatch,
        jobs: Sequence[WebBatchJob],
        event: WebBatchEvent,
    ) -> WebBatch: ...


@dataclass(frozen=True, slots=True)
class WebBatchRecoveryPlan:
    snapshot_path: Path
    snapshot_sha256: str
    batch: WebBatch
    jobs: tuple[WebBatchJob, ...]
    event: WebBatchEvent
    counts: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))


def _require_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"O campo {field} do snapshot deve ser um objeto.")
    return value


def _require_text(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"O campo {field} do snapshot e obrigatorio.")
    return normalized


def _normalized_timestamp(value: Any, *, field: str) -> str:
    raw = _require_text(value, field=field)
    iso_candidate = f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%m/%d/%Y %H:%M:%S")
        except ValueError as exc:
            raise ValueError(
                f"O campo {field} do snapshot deve conter uma data valida."
            ) from exc
    return parsed.isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_snapshot(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ValueError(f"Snapshot de recuperacao nao encontrado: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Snapshot de recuperacao invalido.") from exc
    snapshot = _require_mapping(payload, field="raiz")
    assert_sanitized_payload(snapshot, path="snapshot")
    if snapshot.get("schema_version") != _SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            "schema_version do snapshot de recuperacao nao suportada."
        )
    return snapshot


def _normalized_summary(snapshot: Mapping[str, Any]) -> dict[str, int]:
    summary = _require_mapping(snapshot.get("summary"), field="summary")
    normalized: dict[str, int] = {}
    for legacy_status in _STATUS_MAP:
        raw_value = summary.get(legacy_status)
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise ValueError(
                f"O total summary.{legacy_status} deve ser um inteiro."
            )
        if raw_value < 0:
            raise ValueError(
                f"O total summary.{legacy_status} nao pode ser negativo."
            )
        normalized[legacy_status] = raw_value
    return normalized


def plan_web_batch_recovery(
    snapshot_path: str | Path,
) -> WebBatchRecoveryPlan:
    path = Path(snapshot_path).resolve()
    snapshot = _load_snapshot(path)
    jobs_payload = snapshot.get("jobs")
    if not isinstance(jobs_payload, list) or not jobs_payload:
        raise ValueError("O campo jobs do snapshot deve ser uma lista nao vazia.")

    snapshot_sha256 = _sha256(path)
    batch_id = uuid5(
        NAMESPACE_URL,
        f"tenable-reports:web-batch-recovery:{snapshot_sha256}",
    )
    captured_at = _normalized_timestamp(
        snapshot.get("captured_at"), field="captured_at"
    )
    batch_created_at = _normalized_timestamp(
        snapshot.get("batch_created_at"), field="batch_created_at"
    )
    period = dict(_require_mapping(snapshot.get("period"), field="period"))
    _require_text(period.get("start_at"), field="period.start_at")
    _require_text(period.get("end_at"), field="period.end_at")
    expected_summary = _normalized_summary(snapshot)

    jobs: list[WebBatchJob] = []
    client_ids: set[str] = set()
    original_job_ids: set[UUID] = set()
    actual_legacy_counts = {status: 0 for status in _STATUS_MAP}
    for position, raw_job in enumerate(jobs_payload, start=1):
        job_payload = _require_mapping(raw_job, field=f"jobs[{position - 1}]")
        client_id = _require_text(
            job_payload.get("client_id"),
            field=f"jobs[{position - 1}].client_id",
        )
        if client_id in client_ids:
            raise ValueError("Cliente duplicado no snapshot de recuperacao.")
        client_ids.add(client_id)
        try:
            original_job_id = UUID(
                _require_text(
                    job_payload.get("job_id"),
                    field=f"jobs[{position - 1}].job_id",
                )
            )
        except ValueError as exc:
            raise ValueError("job_id invalido no snapshot de recuperacao.") from exc
        if original_job_id in original_job_ids:
            raise ValueError("job_id duplicado no snapshot de recuperacao.")
        original_job_ids.add(original_job_id)

        legacy_status = _require_text(
            job_payload.get("status"),
            field=f"jobs[{position - 1}].status",
        ).casefold()
        try:
            status = _STATUS_MAP[legacy_status]
        except KeyError as exc:
            raise ValueError(
                f"Status legado nao suportado no snapshot: {legacy_status}."
            ) from exc
        actual_legacy_counts[legacy_status] += 1
        operational_payload = {
            key: job_payload.get(key)
            for key in (
                "chunks_available",
                "partial_manifest_present",
                "remote_status",
                "retry_action",
                "vm_export_uuid",
            )
            if job_payload.get(key) is not None
        }
        operational_payload.update({
            "mode": "manual",
            "days": job_payload.get("days"),
            "start_at": str(job_payload.get("start_at") or period["start_at"]),
            "end_at": str(job_payload.get("end_at") or period["end_at"]),
            "vm_selective_mode": job_payload.get("vm_selective_mode"),
            "vm_export_strategy": job_payload.get("vm_export_strategy"),
            "historical_source": job_payload.get("historical_source"),
            "was_failure_policy": (
                job_payload.get("was_failure_policy") or "retry_then_continue"
            ),
            "force_live_collection": (
                job_payload.get("force_live_collection") is True
            ),
            "confirm_historical_reconstruction": (
                job_payload.get("confirm_historical_reconstruction") is True
            ),
        })
        operational_payload["recovery_original_status"] = legacy_status
        job_id = uuid5(batch_id, str(original_job_id))
        error_code = None
        if status is BatchJobStatus.FAILED:
            error_code = "RECOVERY_SNAPSHOT_FAILED"
        elif status is BatchJobStatus.INTERRUPTED:
            error_code = "RECOVERY_SNAPSHOT_INTERRUPTED"
        jobs.append(
            WebBatchJob(
                id=job_id,
                batch_id=batch_id,
                client_id=client_id,
                position=position,
                status=status,
                attempt_number=1,
                payload=operational_payload,
                logical_job_id=str(original_job_id),
                run_id=(
                    str(job_payload["run_id"])
                    if job_payload.get("run_id")
                    else None
                ),
                error_code=error_code,
                error_message=(
                    str(job_payload["note"])
                    if error_code and job_payload.get("note")
                    else None
                ),
                created_at=batch_created_at,
                started_at=(
                    batch_created_at
                    if status
                    in {
                        BatchJobStatus.COMPLETE,
                        BatchJobStatus.FAILED,
                        BatchJobStatus.INTERRUPTED,
                    }
                    else None
                ),
                ended_at=(
                    captured_at
                    if status
                    in {
                        BatchJobStatus.COMPLETE,
                        BatchJobStatus.FAILED,
                        BatchJobStatus.INTERRUPTED,
                    }
                    else None
                ),
            )
        )

    if actual_legacy_counts != expected_summary:
        raise ValueError(
            "Os totais de summary nao correspondem aos trabalhos do snapshot."
        )
    counts = {
        mapped.value: actual_legacy_counts[legacy]
        for legacy, mapped in _STATUS_MAP.items()
    }
    batch = WebBatch(
        id=batch_id,
        idempotency_key=f"web-batch-recovery:{snapshot_sha256}",
        kind="RECOVERED",
        status=BatchStatus.PAUSED,
        options={
            "mode": "manual",
            "period": period,
            "recovery_snapshot_sha256": snapshot_sha256,
            "recovery_schema_version": _SNAPSHOT_SCHEMA_VERSION,
            "recovery_captured_at": captured_at,
        },
        created_at=batch_created_at,
    )
    event = WebBatchEvent(
        batch_id=batch_id,
        event_type="RECOVERY_SNAPSHOT_IMPORTED",
        payload={"snapshot_sha256": snapshot_sha256, "counts": counts},
        actor="recovery-import",
        idempotency_key=f"web-batch-recovery:{snapshot_sha256}:imported",
        created_at=captured_at,
    )
    return WebBatchRecoveryPlan(
        snapshot_path=path,
        snapshot_sha256=snapshot_sha256,
        batch=batch,
        jobs=tuple(jobs),
        event=event,
        counts=counts,
    )


def apply_web_batch_recovery(
    plan: WebBatchRecoveryPlan,
    repository: WebBatchRecoveryRepository,
) -> WebBatch:
    existing = repository.get_batch(plan.batch.id)
    if existing is not None:
        if existing.idempotency_key != plan.batch.idempotency_key:
            raise ValueError("O identificador do lote recuperado ja esta em uso.")
        return existing
    return repository.import_recovery_batch(
        plan.batch,
        plan.jobs,
        plan.event,
    )


__all__ = [
    "WebBatchRecoveryPlan",
    "apply_web_batch_recovery",
    "plan_web_batch_recovery",
]
