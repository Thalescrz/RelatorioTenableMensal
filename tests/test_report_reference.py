from __future__ import annotations

import unittest


def _domain():
    try:
        from tenable_reports.domain.report_reference import (
            ReferenceKind,
            ReportCandidate,
            ReportOrigin,
            expected_predecessor_key,
            main_eligibility,
            reference_key_for_candidate,
        )
    except ModuleNotFoundError as exc:
        raise AssertionError("report_reference ainda não foi implementado") from exc

    return {
        "ReferenceKind": ReferenceKind,
        "ReportCandidate": ReportCandidate,
        "ReportOrigin": ReportOrigin,
        "expected_predecessor_key": expected_predecessor_key,
        "main_eligibility": main_eligibility,
        "reference_key_for_candidate": reference_key_for_candidate,
    }


def _candidate(*, origin: str, start_at: str, end_at: str, period_mode: str):
    api = _domain()
    return api["ReportCandidate"](
        run_id="run-july",
        client_id="cliente-a",
        tenant_id="tenant-a",
        origin=api["ReportOrigin"](origin),
        execution_type="AUTOMATIC_MONTHLY" if origin != "MANUAL" else "MANUAL",
        period_start_at=start_at,
        period_end_at=end_at,
        period_mode=period_mode,
        timezone="America/Fortaleza",
        scope_hash="scope-v1",
        metric_definition_version="metrics-v1",
        publication_status="READY_FOR_CONTROLLED_DISTRIBUTION",
        documents_valid=True,
    )


class ReportReferenceTests(unittest.TestCase):
    def test_manual_full_calendar_month_shares_automatic_reference_key(self) -> None:
        api = _domain()
        automatic = _candidate(
            origin="SCHEDULED",
            start_at="2026-07-01T03:00:00Z",
            end_at="2026-08-01T03:00:00Z",
            period_mode="PREVIOUS_CALENDAR_MONTH",
        )
        manual = _candidate(
            origin="MANUAL",
            start_at="2026-07-01T03:00:00Z",
            end_at="2026-08-01T03:00:00Z",
            period_mode="EXPLICIT_RANGE",
        )

        self.assertEqual(
            api["reference_key_for_candidate"](automatic),
            api["reference_key_for_candidate"](manual),
        )

    def test_monthly_predecessor_is_immediately_previous_month(self) -> None:
        api = _domain()
        current = api["reference_key_for_candidate"](
            _candidate(
                origin="SCHEDULED",
                start_at="2026-08-01T03:00:00Z",
                end_at="2026-09-01T03:00:00Z",
                period_mode="PREVIOUS_CALENDAR_MONTH",
            )
        )

        predecessor = api["expected_predecessor_key"](current)

        self.assertIsNotNone(predecessor)
        self.assertIs(predecessor.kind, api["ReferenceKind"].MONTHLY)
        self.assertEqual(predecessor.period_key, "2026-07")

    def test_partial_manual_period_is_not_eligible_for_monthly_main(self) -> None:
        api = _domain()
        partial = _candidate(
            origin="MANUAL",
            start_at="2026-07-15T03:00:00Z",
            end_at="2026-08-01T03:00:00Z",
            period_mode="EXPLICIT_RANGE",
        )

        eligibility = api["main_eligibility"](partial)

        self.assertTrue(eligibility.eligible)
        self.assertFalse(eligibility.monthly_eligible)
        self.assertIs(
            api["reference_key_for_candidate"](partial).kind,
            api["ReferenceKind"].EXACT_RANGE,
        )

    def test_invalid_publication_cannot_be_main(self) -> None:
        api = _domain()
        invalid = api["ReportCandidate"](
            run_id="run-invalid",
            client_id="cliente-a",
            tenant_id="tenant-a",
            origin=api["ReportOrigin"].MANUAL,
            execution_type="MANUAL",
            period_start_at="2026-07-01T03:00:00Z",
            period_end_at="2026-08-01T03:00:00Z",
            period_mode="EXPLICIT_RANGE",
            timezone="America/Fortaleza",
            scope_hash="scope-v1",
            metric_definition_version="metrics-v1",
            publication_status="FAILED",
            documents_valid=False,
        )

        eligibility = api["main_eligibility"](invalid)

        self.assertFalse(eligibility.eligible)
        self.assertEqual(
            eligibility.reasons,
            ("PUBLICATION_NOT_READY", "DOCUMENTS_NOT_VALID"),
        )


if __name__ == "__main__":
    unittest.main()
