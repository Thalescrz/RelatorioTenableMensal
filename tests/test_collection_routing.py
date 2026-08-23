from __future__ import annotations

import importlib
import unittest
from datetime import UTC, datetime

from tenable_reports.domain.reporting import (
    explicit_reporting_period,
    previous_calendar_month,
    trailing_days_period,
)


NOW = datetime(2026, 8, 23, 20, 0, tzinfo=UTC)


class CollectionRoutingTests(unittest.TestCase):
    def routing(self):
        try:
            return importlib.import_module("tenable_reports.application.collection_routing")
        except ModuleNotFoundError:
            self.fail("collection_routing ainda nao foi implementado")

    def test_exact_snapshot_is_always_replayed_without_external_collection(self) -> None:
        routing = self.routing()
        period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at=NOW,
        )

        route = routing.select_collection_route(
            period=period,
            now=NOW,
            execution_mode="manual",
            snapshot_available=True,
            historical_source="inventory_beta",
            fallback_policy="fail",
        )

        self.assertEqual(route.source, routing.CollectionSource.SNAPSHOT_REPLAY)
        self.assertEqual(route.accuracy, routing.CollectionAccuracy.AUTHORITATIVE_SNAPSHOT)
        self.assertIsNone(route.warning)

    def test_automatic_previous_month_uses_legacy_vm(self) -> None:
        routing = self.routing()
        period = previous_calendar_month(reference_at="2026-08-01T00:05:00-03:00")

        route = routing.select_collection_route(
            period=period,
            now=datetime(2026, 8, 1, 3, 5, tzinfo=UTC),
            execution_mode="automatic",
            snapshot_available=False,
            historical_source="inventory_beta",
            fallback_policy="fail",
        )

        self.assertEqual(route.source, routing.CollectionSource.LEGACY_VM)
        self.assertEqual(route.accuracy, routing.CollectionAccuracy.AUTHORITATIVE_SNAPSHOT)

    def test_current_rolling_window_uses_legacy_vm(self) -> None:
        routing = self.routing()
        period = trailing_days_period(30, reference_at=NOW)

        route = routing.select_collection_route(
            period=period,
            now=NOW,
            execution_mode="manual",
            snapshot_available=False,
            historical_source="inventory_beta",
            fallback_policy="fail",
        )

        self.assertEqual(route.source, routing.CollectionSource.LEGACY_VM)
        self.assertEqual(route.accuracy, routing.CollectionAccuracy.CURRENT_WINDOW)

    def test_closed_historical_period_uses_bounded_inventory_when_enabled(self) -> None:
        routing = self.routing()
        period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at=NOW,
        )

        route = routing.select_collection_route(
            period=period,
            now=NOW,
            execution_mode="manual",
            snapshot_available=False,
            historical_source="inventory_beta",
            fallback_policy="fail",
        )

        self.assertEqual(route.source, routing.CollectionSource.INVENTORY_BOUNDED)
        self.assertEqual(
            route.accuracy,
            routing.CollectionAccuracy.HISTORICAL_RECONSTRUCTION,
        )
        self.assertIn("reconstrucao", route.warning.lower())

    def test_closed_historical_period_can_fall_back_to_legacy_with_warning(self) -> None:
        routing = self.routing()
        period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at=NOW,
        )

        route = routing.select_collection_route(
            period=period,
            now=NOW,
            execution_mode="manual",
            snapshot_available=False,
            historical_source="legacy",
            fallback_policy="warn_legacy",
        )

        self.assertEqual(route.source, routing.CollectionSource.LEGACY_VM)
        self.assertEqual(
            route.accuracy,
            routing.CollectionAccuracy.HISTORICAL_RECONSTRUCTION,
        )
        self.assertIn("limite superior", route.warning.lower())

    def test_closed_historical_period_can_fail_instead_of_silent_fallback(self) -> None:
        routing = self.routing()
        period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at=NOW,
        )

        with self.assertRaisesRegex(ValueError, "Inventory API"):
            routing.select_collection_route(
                period=period,
                now=NOW,
                execution_mode="manual",
                snapshot_available=False,
                historical_source="legacy",
                fallback_policy="fail",
            )


if __name__ == "__main__":
    unittest.main()
