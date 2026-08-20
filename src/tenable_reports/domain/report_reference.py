from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


READY_STATUS = "READY_FOR_CONTROLLED_DISTRIBUTION"
MONTHLY_CANONICAL_MODE = "MONTHLY_CANONICAL"


class ReportOrigin(StrEnum):
    SCHEDULED = "SCHEDULED"
    AUTOMATIC_RETRY = "AUTOMATIC_RETRY"
    MANUAL = "MANUAL"


class ReferenceKind(StrEnum):
    MONTHLY = "MONTHLY"
    EXACT_RANGE = "EXACT_RANGE"


@dataclass(frozen=True, slots=True)
class ReportCandidate:
    run_id: str
    client_id: str
    tenant_id: str
    origin: ReportOrigin
    execution_type: str
    period_start_at: str
    period_end_at: str
    period_mode: str
    timezone: str
    scope_hash: str
    metric_definition_version: str
    publication_status: str
    documents_valid: bool
    deleted_at: str | None = None


@dataclass(frozen=True, slots=True)
class ReportReferenceKey:
    client_id: str
    tenant_id: str
    kind: ReferenceKind
    period_key: str
    period_mode: str
    timezone: str
    scope_hash: str
    metric_definition_version: str

    @property
    def stable_key(self) -> str:
        payload = {
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "kind": self.kind.value,
            "period_key": self.period_key,
            "period_mode": self.period_mode,
            "timezone": self.timezone,
            "scope_hash": self.scope_hash,
            "metric_definition_version": self.metric_definition_version,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    monthly_eligible: bool
    reasons: tuple[str, ...]


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Limite de período inválido; use ISO-8601.") from exc
    if parsed.tzinfo is None:
        raise ValueError("Limites do período precisam conter timezone.")
    return parsed.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Timezone IANA inválido: {name}") from exc


def _next_month_start(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _previous_month(value: datetime) -> datetime:
    if value.month == 1:
        return value.replace(year=value.year - 1, month=12)
    return value.replace(month=value.month - 1)


def _is_full_calendar_month(start: datetime, end: datetime, timezone_name: str) -> bool:
    zone = _zone(timezone_name)
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    if any(
        (
            local_start.day != 1,
            local_start.hour != 0,
            local_start.minute != 0,
            local_start.second != 0,
            local_start.microsecond != 0,
            local_end.day != 1,
            local_end.hour != 0,
            local_end.minute != 0,
            local_end.second != 0,
            local_end.microsecond != 0,
        )
    ):
        return False
    return local_end == _next_month_start(local_start)


def reference_key_for_candidate(candidate: ReportCandidate) -> ReportReferenceKey:
    start = _parse_utc(candidate.period_start_at)
    end = _parse_utc(candidate.period_end_at)
    if start >= end:
        raise ValueError("O início do período precisa ser anterior ao fim.")
    if _is_full_calendar_month(start, end, candidate.timezone):
        local_start = start.astimezone(_zone(candidate.timezone))
        kind = ReferenceKind.MONTHLY
        period_key = local_start.strftime("%Y-%m")
        period_mode = MONTHLY_CANONICAL_MODE
    else:
        kind = ReferenceKind.EXACT_RANGE
        period_key = f"{_iso_utc(start)}/{_iso_utc(end)}"
        period_mode = candidate.period_mode
    return ReportReferenceKey(
        client_id=candidate.client_id,
        tenant_id=candidate.tenant_id,
        kind=kind,
        period_key=period_key,
        period_mode=period_mode,
        timezone=candidate.timezone,
        scope_hash=candidate.scope_hash,
        metric_definition_version=candidate.metric_definition_version,
    )


def expected_predecessor_key(
    current: ReportReferenceKey,
) -> ReportReferenceKey | None:
    if current.kind is ReferenceKind.MONTHLY:
        try:
            current_month = datetime.strptime(current.period_key, "%Y-%m")
        except ValueError as exc:
            raise ValueError("Chave mensal inválida; use YYYY-MM.") from exc
        previous = _previous_month(current_month)
        period_key = previous.strftime("%Y-%m")
    else:
        try:
            start_text, end_text = current.period_key.split("/", maxsplit=1)
        except ValueError as exc:
            raise ValueError("Chave de intervalo exato inválida.") from exc
        start = _parse_utc(start_text)
        end = _parse_utc(end_text)
        duration = end - start
        if duration.total_seconds() <= 0:
            raise ValueError("Chave de intervalo exato possui duração inválida.")
        period_key = f"{_iso_utc(start - duration)}/{_iso_utc(start)}"
    return ReportReferenceKey(
        client_id=current.client_id,
        tenant_id=current.tenant_id,
        kind=current.kind,
        period_key=period_key,
        period_mode=current.period_mode,
        timezone=current.timezone,
        scope_hash=current.scope_hash,
        metric_definition_version=current.metric_definition_version,
    )


def main_eligibility(candidate: ReportCandidate) -> Eligibility:
    reasons: list[str] = []
    if candidate.publication_status != READY_STATUS:
        reasons.append("PUBLICATION_NOT_READY")
    if not candidate.documents_valid:
        reasons.append("DOCUMENTS_NOT_VALID")
    if candidate.deleted_at:
        reasons.append("REPORT_DELETED")
    if reasons:
        return Eligibility(
            eligible=False,
            monthly_eligible=False,
            reasons=tuple(reasons),
        )
    try:
        key = reference_key_for_candidate(candidate)
    except ValueError:
        return Eligibility(
            eligible=False,
            monthly_eligible=False,
            reasons=("REFERENCE_METADATA_INVALID",),
        )
    return Eligibility(
        eligible=not reasons,
        monthly_eligible=not reasons and key.kind is ReferenceKind.MONTHLY,
        reasons=tuple(reasons),
    )
