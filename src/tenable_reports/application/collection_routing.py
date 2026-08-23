from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from tenable_reports.domain.reporting import PeriodMode, ReportingPeriod


class CollectionSource(StrEnum):
    SNAPSHOT_REPLAY = "snapshot_replay"
    LEGACY_VM = "legacy_vm"
    INVENTORY_BOUNDED = "inventory_bounded"


class CollectionAccuracy(StrEnum):
    AUTHORITATIVE_SNAPSHOT = "authoritative_snapshot"
    CURRENT_WINDOW = "current_window"
    HISTORICAL_RECONSTRUCTION = "historical_reconstruction"


@dataclass(frozen=True, slots=True)
class CollectionRoute:
    source: CollectionSource
    accuracy: CollectionAccuracy
    reason: str
    warning: str | None = None


def select_collection_route(
    *,
    period: ReportingPeriod,
    now: datetime,
    execution_mode: str,
    snapshot_available: bool,
    historical_source: str,
    fallback_policy: str,
) -> CollectionRoute:
    current_time = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

    if snapshot_available:
        return CollectionRoute(
            source=CollectionSource.SNAPSHOT_REPLAY,
            accuracy=CollectionAccuracy.AUTHORITATIVE_SNAPSHOT,
            reason="exact_period_snapshot_available",
        )

    if (
        execution_mode.strip().lower() == "automatic"
        and period.mode is PeriodMode.PREVIOUS_CALENDAR_MONTH
    ):
        return CollectionRoute(
            source=CollectionSource.LEGACY_VM,
            accuracy=CollectionAccuracy.AUTHORITATIVE_SNAPSHOT,
            reason="scheduled_previous_calendar_month",
        )

    if period.end_at >= current_time - timedelta(minutes=5):
        return CollectionRoute(
            source=CollectionSource.LEGACY_VM,
            accuracy=CollectionAccuracy.CURRENT_WINDOW,
            reason="period_ends_at_current_execution",
        )

    if historical_source.strip().lower() == "inventory_beta":
        return CollectionRoute(
            source=CollectionSource.INVENTORY_BOUNDED,
            accuracy=CollectionAccuracy.HISTORICAL_RECONSTRUCTION,
            reason="closed_historical_period_without_snapshot",
            warning=(
                "Reconstrucao historica: o estado atual dos findings pode diferir "
                "do estado observado no encerramento do periodo."
            ),
        )

    if fallback_policy.strip().lower() == "fail":
        raise ValueError(
            "Inventory API precisa estar habilitada para reconstruir este periodo "
            "historico sem snapshot."
        )

    return CollectionRoute(
        source=CollectionSource.LEGACY_VM,
        accuracy=CollectionAccuracy.HISTORICAL_RECONSTRUCTION,
        reason="legacy_historical_fallback",
        warning=(
            "Reconstrucao historica aproximada: o export VM legado nao aplica "
            "limite superior na origem."
        ),
    )
