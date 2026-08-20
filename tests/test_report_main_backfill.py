from dataclasses import replace

from tenable_reports.application.report_main_backfill import plan_main_backfill
from tenable_reports.application.report_registry import InMemoryReportRegistry
from tenable_reports.domain.report_reference import (
    READY_STATUS,
    ReportCandidate,
    ReportOrigin,
    reference_key_for_candidate,
)


def valid_run(run_id: str) -> ReportCandidate:
    return ReportCandidate(
        run_id=run_id, client_id="cliente-a", tenant_id="tenant-a",
        origin=ReportOrigin.MANUAL, execution_type="MANUAL",
        period_start_at="2026-07-01T03:00:00Z",
        period_end_at="2026-08-01T03:00:00Z",
        period_mode="PREVIOUS_CALENDAR_MONTH", timezone="America/Fortaleza",
        scope_hash="scope", metric_definition_version="report-definition-v1.2",
        publication_status=READY_STATUS, documents_valid=True,
    )


KEY = reference_key_for_candidate(valid_run("reference"))


def test_single_valid_run_is_auto_selected() -> None:
    plan = plan_main_backfill([valid_run("run-a")], used_history_run_ids=set())
    assert plan.promotions == ((KEY, "run-a"),)


def test_history_used_run_wins_when_multiple_candidates_exist() -> None:
    plan = plan_main_backfill(
        [valid_run("run-a"), valid_run("run-b")],
        used_history_run_ids={"run-a"},
    )
    assert plan.promotions == ((KEY, "run-a"),)


def test_ambiguous_candidates_require_analyst_selection() -> None:
    plan = plan_main_backfill(
        [valid_run("run-a"), valid_run("run-b")], used_history_run_ids=set()
    )
    assert plan.promotions == ()
    assert plan.alerts[0].code == "MAIN_SELECTION_REQUIRED"


def test_invalid_or_deleted_candidates_are_reported_but_never_promoted() -> None:
    invalid = valid_run("run-invalid")
    invalid = replace(invalid, documents_valid=False)
    plan = plan_main_backfill([invalid], used_history_run_ids=set())
    assert plan.promotions == ()
    assert plan.invalid[0].run_id == "run-invalid"


def test_soft_deleted_registered_report_is_never_promoted() -> None:
    registry = InMemoryReportRegistry()
    registry.register_report(valid_run("run-deleted"))
    registry.soft_delete(
        "run-deleted", actor="analista", reason="inválido", allow_gap=True
    )
    plan = plan_main_backfill(
        registry.list_reports(include_deleted=True), used_history_run_ids=set()
    )
    assert plan.promotions == ()
    assert plan.invalid[0].reasons == ("REPORT_DELETED",)


def test_existing_main_protects_its_entire_reference_from_backfill() -> None:
    plan = plan_main_backfill(
        [valid_run("run-main"), valid_run("run-alternate")],
        used_history_run_ids=set(),
        existing_main_run_ids={"run-main"},
    )
    assert plan.promotions == ()
    assert plan.alerts == ()
    assert plan.already_selected_run_ids == ("run-main",)


def test_legacy_candidate_with_missing_reference_metadata_is_ignored() -> None:
    legacy = replace(
        valid_run("run-legacy"),
        timezone="",
        period_mode="",
        scope_hash="",
        metric_definition_version="",
        documents_valid=False,
    )
    plan = plan_main_backfill([legacy], used_history_run_ids=set())
    assert plan.promotions == ()
    assert plan.invalid[0].run_id == "run-legacy"
    assert "DOCUMENTS_NOT_VALID" in plan.invalid[0].reasons


def test_malformed_reference_metadata_is_an_eligibility_reason() -> None:
    malformed = replace(valid_run("run-malformed"), timezone="")
    plan = plan_main_backfill([malformed], used_history_run_ids=set())
    assert plan.promotions == ()
    assert plan.invalid[0].reasons == ("REFERENCE_METADATA_INVALID",)
