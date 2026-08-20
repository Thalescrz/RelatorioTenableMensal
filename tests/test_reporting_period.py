from __future__ import annotations

import unittest

from tenable_reports.domain.reporting import (
    explicit_reporting_period,
    manual_rolling_month,
    PeriodMode,
    previous_calendar_month,
    resolve_manual_period,
    trailing_days_period,
)


class ReportingPeriodTests(unittest.TestCase):
    def test_default_is_previous_calendar_month_not_last_30_days(self) -> None:
        period = previous_calendar_month(
            reference_at="2026-08-12T10:30:00-03:00",
            timezone_name="America/Fortaleza",
        )
        self.assertEqual(period.mode, PeriodMode.PREVIOUS_CALENDAR_MONTH)
        self.assertEqual(period.period_id, "2026-07")
        self.assertEqual(period.to_dict()["start_at"], "2026-07-01T03:00:00Z")
        self.assertEqual(period.to_dict()["end_at"], "2026-08-01T03:00:00Z")
        self.assertEqual(period.to_dict()["interval"], "[start_at, end_at)")

    def test_january_rolls_back_to_previous_year(self) -> None:
        period = previous_calendar_month(
            reference_at="2027-01-01T01:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        self.assertEqual(period.period_id, "2026-12")

    def test_trailing_days_is_explicit_and_ends_at_reference_instant(self) -> None:
        period = trailing_days_period(
            30,
            reference_at="2026-08-12T10:30:00-03:00",
            timezone_name="America/Fortaleza",
        )
        self.assertEqual(period.mode, PeriodMode.TRAILING_DAYS)
        self.assertEqual(period.to_dict()["end_at"], "2026-08-12T13:30:00Z")
        self.assertEqual(period.to_dict()["start_at"], "2026-07-13T13:30:00Z")

    def test_manual_default_is_one_calendar_month_until_execution(self) -> None:
        period = manual_rolling_month(
            reference_at="2026-08-13T10:30:00-03:00",
            timezone_name="America/Fortaleza",
        )
        self.assertEqual(period.mode, PeriodMode.MANUAL_ROLLING_MONTH)
        self.assertEqual(period.to_dict()["start_at"], "2026-07-13T13:30:00Z")
        self.assertEqual(period.to_dict()["end_at"], "2026-08-13T13:30:00Z")

    def test_manual_month_clamps_day_at_shorter_month(self) -> None:
        period = manual_rolling_month(
            reference_at="2026-03-31T10:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        self.assertEqual(period.to_dict()["start_at"], "2026-02-28T13:00:00Z")

    def test_explicit_period_uses_exclusive_end(self) -> None:
        period = explicit_reporting_period(
            start_at="2026-06-01T00:00:00-03:00",
            end_at="2026-07-01T00:00:00-03:00",
            reference_at="2026-08-13T10:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        self.assertEqual(period.mode, PeriodMode.EXPLICIT_RANGE)
        self.assertEqual(period.to_dict()["start_at"], "2026-06-01T03:00:00Z")
        self.assertEqual(period.to_dict()["end_at"], "2026-07-01T03:00:00Z")

    def test_manual_period_rejects_conflicting_or_incomplete_selection(self) -> None:
        with self.assertRaises(ValueError):
            resolve_manual_period(
                timezone_name="America/Fortaleza",
                reference_at="2026-08-13T10:00:00-03:00",
                days=10,
                start_at="2026-07-01T00:00:00-03:00",
                end_at="2026-08-01T00:00:00-03:00",
            )
        with self.assertRaises(ValueError):
            resolve_manual_period(
                timezone_name="America/Fortaleza",
                reference_at="2026-08-13T10:00:00-03:00",
                start_at="2026-07-01T00:00:00-03:00",
            )

    def test_invalid_days_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            trailing_days_period(0, reference_at="2026-08-12")


if __name__ == "__main__":
    unittest.main()
