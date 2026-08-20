from __future__ import annotations

from dataclasses import asdict, dataclass
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PeriodMode(StrEnum):
    PREVIOUS_CALENDAR_MONTH = "PREVIOUS_CALENDAR_MONTH"
    MANUAL_ROLLING_MONTH = "MANUAL_ROLLING_MONTH"
    TRAILING_DAYS = "TRAILING_DAYS"
    EXPLICIT_RANGE = "EXPLICIT_RANGE"


def parse_datetime(value: str | datetime | date | None, timezone_name: str) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Timezone IANA invalido: {timezone_name}") from exc
    if value is None:
        return datetime.now(UTC).astimezone(zone)
    if isinstance(value, datetime):
        return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=zone)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Data/hora invalida; use ISO-8601.") from exc
    return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ReportingPeriod:
    start_at: datetime
    end_at: datetime
    timezone: str
    mode: PeriodMode
    reference_at: datetime
    trailing_days: int | None = None

    def __post_init__(self) -> None:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("O periodo precisa conter timezone.")
        if self.start_at >= self.end_at:
            raise ValueError("O inicio do periodo precisa ser anterior ao fim.")

    @property
    def start_epoch(self) -> int:
        return int(self.start_at.astimezone(UTC).timestamp())

    @property
    def end_epoch(self) -> int:
        return int(self.end_at.astimezone(UTC).timestamp())

    @property
    def period_id(self) -> str:
        local_start = self.start_at.astimezone(ZoneInfo(self.timezone))
        local_end = self.end_at.astimezone(ZoneInfo(self.timezone))
        if (
            self.mode is PeriodMode.PREVIOUS_CALENDAR_MONTH
            and local_start.day == 1
            and local_end.day == 1
        ):
            return local_start.strftime("%Y-%m")
        return f"{local_start:%Y%m%dT%H%M%S}-{local_end:%Y%m%dT%H%M%S}"

    def contains(self, value: datetime | None) -> bool:
        return value is not None and self.start_at <= value < self.end_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_id": self.period_id,
            "mode": self.mode.value,
            "timezone": self.timezone,
            "start_at": iso_utc(self.start_at),
            "end_at": iso_utc(self.end_at),
            "interval": "[start_at, end_at)",
            "reference_at": iso_utc(self.reference_at),
            "trailing_days": self.trailing_days,
        }


def previous_calendar_month(
    *,
    reference_at: str | datetime | date | None = None,
    timezone_name: str = "America/Fortaleza",
) -> ReportingPeriod:
    reference = parse_datetime(reference_at, timezone_name)
    zone = ZoneInfo(timezone_name)
    current_month = datetime(reference.year, reference.month, 1, tzinfo=zone)
    previous_day = current_month - timedelta(days=1)
    previous_month = datetime(previous_day.year, previous_day.month, 1, tzinfo=zone)
    return ReportingPeriod(
        start_at=previous_month.astimezone(UTC),
        end_at=current_month.astimezone(UTC),
        timezone=timezone_name,
        mode=PeriodMode.PREVIOUS_CALENDAR_MONTH,
        reference_at=reference.astimezone(UTC),
    )


def trailing_days_period(
    days: int,
    *,
    reference_at: str | datetime | date | None = None,
    timezone_name: str = "America/Fortaleza",
) -> ReportingPeriod:
    if not 1 <= int(days) <= 3660:
        raise ValueError("days deve estar entre 1 e 3660.")
    reference = parse_datetime(reference_at, timezone_name)
    end = reference.astimezone(UTC)
    return ReportingPeriod(
        start_at=end - timedelta(days=int(days)),
        end_at=end,
        timezone=timezone_name,
        mode=PeriodMode.TRAILING_DAYS,
        reference_at=end,
        trailing_days=int(days),
    )


def manual_rolling_month(
    *,
    reference_at: str | datetime | date | None = None,
    timezone_name: str = "America/Fortaleza",
) -> ReportingPeriod:
    """Um mês-calendário móvel, encerrado no instante da execução manual."""
    reference = parse_datetime(reference_at, timezone_name)
    previous_year = reference.year if reference.month > 1 else reference.year - 1
    previous_month = reference.month - 1 if reference.month > 1 else 12
    previous_day = min(reference.day, monthrange(previous_year, previous_month)[1])
    start = reference.replace(
        year=previous_year,
        month=previous_month,
        day=previous_day,
    )
    return ReportingPeriod(
        start_at=start.astimezone(UTC),
        end_at=reference.astimezone(UTC),
        timezone=timezone_name,
        mode=PeriodMode.MANUAL_ROLLING_MONTH,
        reference_at=reference.astimezone(UTC),
    )


def explicit_reporting_period(
    *,
    start_at: str | datetime | date,
    end_at: str | datetime | date,
    reference_at: str | datetime | date | None = None,
    timezone_name: str = "America/Fortaleza",
) -> ReportingPeriod:
    """Intervalo manual explícito; o fim é exclusivo e não pode estar no futuro."""
    reference = parse_datetime(reference_at, timezone_name)
    start = parse_datetime(start_at, timezone_name)
    end = parse_datetime(end_at, timezone_name)
    if end > reference:
        raise ValueError("end_at nao pode ser posterior ao instante da execucao.")
    return ReportingPeriod(
        start_at=start.astimezone(UTC),
        end_at=end.astimezone(UTC),
        timezone=timezone_name,
        mode=PeriodMode.EXPLICIT_RANGE,
        reference_at=reference.astimezone(UTC),
    )


def resolve_manual_period(
    *,
    timezone_name: str,
    reference_at: str | datetime | date | None = None,
    days: int | None = None,
    start_at: str | datetime | date | None = None,
    end_at: str | datetime | date | None = None,
) -> ReportingPeriod:
    has_explicit_boundary = start_at is not None or end_at is not None
    if days is not None and has_explicit_boundary:
        raise ValueError("Use --days ou --start-at/--end-at, nunca os dois modos juntos.")
    if has_explicit_boundary:
        if start_at is None or end_at is None:
            raise ValueError("--start-at e --end-at precisam ser informados juntos.")
        return explicit_reporting_period(
            start_at=start_at,
            end_at=end_at,
            reference_at=reference_at,
            timezone_name=timezone_name,
        )
    if days is not None:
        return trailing_days_period(
            days,
            reference_at=reference_at,
            timezone_name=timezone_name,
        )
    return manual_rolling_month(
        reference_at=reference_at,
        timezone_name=timezone_name,
    )


def resolve_reporting_period(
    *,
    timezone_name: str,
    reference_at: str | datetime | date | None = None,
    days: int | None = None,
) -> ReportingPeriod:
    """Compatibilidade: resolve a política automática mensal anterior."""
    if days is not None:
        raise ValueError("A execucao automatica nao aceita --days; use o fluxo manual.")
    return previous_calendar_month(
        reference_at=reference_at,
        timezone_name=timezone_name,
    )
