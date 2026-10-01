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

    def test_partial_explicit_period_uses_exclusive_end(self) -> None:
        period = explicit_reporting_period(
            start_at="2026-06-02T00:00:00-03:00",
            end_at="2026-07-01T00:00:00-03:00",
            reference_at="2026-08-13T10:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        self.assertEqual(period.mode, PeriodMode.EXPLICIT_RANGE)
        self.assertEqual(period.to_dict()["start_at"], "2026-06-02T03:00:00Z")
        self.assertEqual(period.to_dict()["end_at"], "2026-07-01T03:00:00Z")

    def test_completed_full_month_is_monthly_cutoff_competence(self) -> None:
        period = explicit_reporting_period(
            start_at="2026-09-01T00:00:00-03:00",
            end_at="2026-10-01T00:00:00-03:00",
            reference_at="2026-10-01T08:00:00-03:00",
            timezone_name="America/Fortaleza",
        )

        self.assertEqual(period.mode, PeriodMode.MONTHLY_CUTOFF)
        self.assertEqual(period.period_id, "2026-09")
        self.assertEqual(period.to_dict()["end_at"], "2026-10-01T03:00:00Z")

    def test_explicit_period_clips_current_inclusive_day_to_reference_instant(self) -> None:
        period = explicit_reporting_period(
            start_at="2026-09-01",
            end_at="2026-10-01",
            reference_at="2026-09-30T16:36:04-03:00",
            timezone_name="America/Fortaleza",
        )

        self.assertEqual(period.mode, PeriodMode.MONTHLY_CUTOFF)
        self.assertEqual(period.period_id, "2026-09")
        self.assertEqual(period.to_dict()["start_at"], "2026-09-01T03:00:00Z")
        self.assertEqual(period.to_dict()["end_at"], "2026-09-30T19:36:04Z")
        self.assertEqual(period.to_dict()["reference_at"], "2026-09-30T19:36:04Z")

    def test_partial_current_day_range_remains_exact(self) -> None:
        period = explicit_reporting_period(
            start_at="2026-09-15",
            end_at="2026-10-01",
            reference_at="2026-09-30T16:36:04-03:00",
            timezone_name="America/Fortaleza",
        )

        self.assertEqual(period.mode, PeriodMode.EXPLICIT_RANGE)
        self.assertNotEqual(period.period_id, "2026-09")

    def test_explicit_period_rejects_future_boundary_beyond_current_day(self) -> None:
        with self.assertRaisesRegex(ValueError, "posterior"):
            explicit_reporting_period(
                start_at="2026-09-01",
                end_at="2026-10-02",
                reference_at="2026-09-30T16:36:04-03:00",
                timezone_name="America/Fortaleza",
            )

    def test_current_day_clip_uses_client_timezone_across_dst_and_utc_date(self) -> None:
        cases = (
            (
                "America/New_York",
                "2026-03-01",
                "2026-03-08T12:00:00-04:00",
                "2026-03-09",
                "2026-03-08T16:00:00Z",
            ),
            (
                "Asia/Tokyo",
                "2026-09-01",
                "2026-09-30T01:00:00+09:00",
                "2026-10-01",
                "2026-09-29T16:00:00Z",
            ),
        )

        for timezone_name, start_at, reference_at, end_at, expected_end in cases:
            with self.subTest(timezone_name=timezone_name):
                period = explicit_reporting_period(
                    start_at=start_at,
                    end_at=end_at,
                    reference_at=reference_at,
                    timezone_name=timezone_name,
                )

                self.assertEqual(period.to_dict()["end_at"], expected_end)

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
