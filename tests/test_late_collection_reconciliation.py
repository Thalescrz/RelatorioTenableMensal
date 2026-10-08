from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from tenable_reports.domain.report_dataset import build_report_dataset
from tenable_reports.domain.reporting import previous_calendar_month
from tests.test_report_dataset import normalized_fixture


def _period():
    return previous_calendar_month(
        reference_at="2026-08-05T10:00:00-03:00",
        timezone_name="America/Fortaleza",
    )


def _rows():
    normalized = normalized_fixture()
    asset = replace(
        normalized.assets[0],
        source_asset_id="asset-fixture",
        asset_key="client-fixture:tenable_vm:asset-fixture",
        first_scan_at="2026-06-01T10:00:00Z",
        last_scan_at="2026-08-03T10:00:00Z",
    )
    finding = replace(
        normalized.findings[0],
        finding_key="finding-open-late",
        source_asset_id=asset.source_asset_id,
        asset_key=asset.asset_key,
        state="OPEN",
        severity="CRITICAL",
        first_found_at="2026-06-15T10:00:00Z",
        last_found_at="2026-08-03T09:00:00Z",
        resurfaced_at=None,
    )
    return asset, finding


def _build(*, findings, completed_at: datetime, include_info: bool = False):
    asset, _ = _rows()
    return build_report_dataset(
        client_id="client-fixture",
        run_id="run-late-fixture",
        execution_type="AUTOMATIC_MONTHLY",
        period=_period(),
        assets=(asset,),
        findings=findings,
        generated_at=completed_at,
        collection_completed_at=completed_at,
        finding_query={"filters": {"state": ["OPEN", "REOPENED", "FIXED"]}},
        include_info_severity=include_info,
        late_collection_grace_days=1,
        tag_scope={
            "selected_tags": [
                {
                    "uuid": "tag-fixture",
                    "category_name": "Equipe",
                    "value": "Infra",
                    "asset_ids": [asset.source_asset_id],
                }
            ]
        },
    )


def test_late_collection_reconciles_only_confirmed_open_population() -> None:
    asset, opened = _rows()
    eligible_reopened = replace(
        opened,
        finding_key="finding-reopened-before-close",
        state="REOPENED",
        severity="HIGH",
        resurfaced_at="2026-07-20T10:00:00Z",
    )
    reopened_after_close = replace(
        opened,
        finding_key="finding-reopened-after-close",
        state="REOPENED",
        severity="MEDIUM",
        resurfaced_at="2026-08-02T10:00:00Z",
    )
    orphan = replace(
        opened,
        finding_key="finding-orphan",
        asset_key=None,
        source_asset_id="missing-asset",
        severity="LOW",
    )
    informational = replace(
        opened,
        finding_key="finding-info",
        severity="INFO",
    )
    period = _period()
    result = _build(
        findings=(opened, eligible_reopened, reopened_after_close, orphan, informational),
        completed_at=period.end_at + timedelta(days=2),
    )

    assert result.dataset.metrics["non_mitigated"]["total"] == 2
    assert result.dataset.metrics["non_mitigated"]["by_severity"] == {
        "critical": 1,
        "high": 1,
        "medium": 0,
        "low": 0,
    }
    assert {item.finding_key for item in result.included_findings} == {
        opened.finding_key,
        eligible_reopened.finding_key,
    }
    reconciliation = result.dataset.collection_timing["reconciliation"]
    assert reconciliation == {
        "status": "APPLIED",
        "method": "confirmed_open_temporal_continuity",
        "adjusted_open_findings": 2,
        "by_severity": {
            "critical": 1,
            "high": 1,
            "medium": 0,
            "low": 0,
        },
    }
    tag_rows = result.dataset.customizations["network_tag_snapshots"][0]["assets"]
    assert tag_rows[0]["total"] == 2


def test_collection_at_grace_boundary_does_not_reconcile() -> None:
    _, opened = _rows()
    period = _period()

    result = _build(
        findings=(opened,),
        completed_at=period.end_at + timedelta(days=1),
    )

    assert result.dataset.collection_timing["status"] == "ON_TIME"
    assert "reconciliation" not in result.dataset.collection_timing
    assert result.dataset.metrics["non_mitigated"]["total"] == 0


def test_late_collection_keeps_informational_when_profile_explicitly_allows_it() -> None:
    _, opened = _rows()
    informational = replace(opened, finding_key="finding-info", severity="INFO")
    period = _period()

    result = _build(
        findings=(informational,),
        completed_at=period.end_at + timedelta(days=2),
        include_info=True,
    )

    assert result.dataset.metrics["non_mitigated"]["total"] == 1
    assert result.dataset.collection_timing["reconciliation"][
        "adjusted_open_findings"
    ] == 1

