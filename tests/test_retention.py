from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tenable_reports.application.retention import (
    RetentionPolicy,
    apply_cleanup_plan,
    plan_orchestration_log_cleanup,
    plan_published_run_cleanup,
    plan_tiered_retention,
)


POLICY = RetentionPolicy(
    failed_raw_days=7,
    successful_raw_days=60,
    normalized_days=90,
    documents_days=395,
)
NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def _run(root: Path, category: str, run_id: str, age_days: int) -> Path:
    path = root / category / "cliente-a" / run_id
    path.mkdir(parents=True)
    timestamp = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (timestamp, timestamp))
    return path


def test_failed_raw_is_candidate_after_seven_days(tmp_path: Path) -> None:
    _run(tmp_path, "raw", "run-failed", 8)

    plan = plan_tiered_retention(
        scoped_output_root=tmp_path,
        policy=POLICY,
        run_status={"run-failed": "FAILED"},
        history_confirmed_run_ids={"run-failed"},
        now=NOW,
    )

    assert {(item.category, item.run_id) for item in plan.candidates} == {
        ("raw", "run-failed")
    }


def test_main_documents_and_dataset_are_protected(tmp_path: Path) -> None:
    for category in ("reports", "report-datasets"):
        _run(tmp_path, category, "run-main", 500)

    plan = plan_tiered_retention(
        scoped_output_root=tmp_path,
        policy=POLICY,
        run_status={"run-main": "COMPLETE"},
        history_confirmed_run_ids={"run-main"},
        main_run_ids={"run-main"},
        now=NOW,
    )

    assert not plan.candidates
    assert {
        (item.category, item.reason) for item in plan.skipped
    } == {
        ("reports", "MAIN_REFERENCE_PROTECTED"),
        ("report-datasets", "MAIN_REFERENCE_PROTECTED"),
    }


def test_raw_can_expire_for_main_after_history_is_confirmed(tmp_path: Path) -> None:
    _run(tmp_path, "raw", "run-main", 61)

    plan = plan_tiered_retention(
        scoped_output_root=tmp_path,
        policy=POLICY,
        run_status={"run-main": "COMPLETE"},
        history_confirmed_run_ids={"run-main"},
        main_run_ids={"run-main"},
        now=NOW,
    )

    assert [(item.category, item.run_id) for item in plan.candidates] == [
        ("raw", "run-main")
    ]


def test_retry_required_and_unconfirmed_runs_are_never_removed(tmp_path: Path) -> None:
    _run(tmp_path, "raw", "run-retry", 100)
    _run(tmp_path, "normalized", "run-unconfirmed", 100)

    plan = plan_tiered_retention(
        scoped_output_root=tmp_path,
        policy=POLICY,
        run_status={"run-retry": "FAILED", "run-unconfirmed": "COMPLETE"},
        retry_required_run_ids={"run-retry"},
        now=NOW,
    )

    assert not plan.candidates
    assert {item.reason for item in plan.skipped} == {
        "RETRY_REQUIRED",
        "HISTORY_NOT_CONFIRMED",
    }


def test_published_run_cleanup_removes_only_transient_categories(tmp_path: Path) -> None:
    for category in ("raw", "snapshots", "normalized", "report-datasets", "reports"):
        path = tmp_path / category / "cliente-a" / "run-a"
        path.mkdir(parents=True)
        (path / "fixture.bin").write_bytes(b"fixture")

    plan = plan_published_run_cleanup(
        scoped_output_root=tmp_path,
        client_id="cliente-a",
        run_id="run-a",
        publication_confirmed=True,
        history_confirmed=True,
    )
    result = apply_cleanup_plan(
        scoped_output_root=tmp_path,
        candidates=plan.candidates,
    )

    assert {path.parent.parent.name for path in result.removed} == {
        "raw", "snapshots", "normalized", "report-datasets"
    }
    assert result.removed_bytes == 4 * len(b"fixture")
    assert result.status == "COMPLETE"
    assert (tmp_path / "reports" / "cliente-a" / "run-a").is_dir()


def test_failed_cloud_staging_is_not_cleaned_before_retry_window(
    tmp_path: Path,
) -> None:
    for category in ("raw", "snapshots", "normalized", "report-datasets"):
        path = tmp_path / category / "cliente-a" / "run-a"
        path.mkdir(parents=True)
        (path / "fixture.bin").write_bytes(b"fixture")
    cloud_staging = (
        tmp_path / "raw" / "cliente-a" / "run-a" / "tenable_cloud"
    )
    cloud_staging.mkdir()
    (cloud_staging / "checkpoint.json").write_text("{}", encoding="utf-8")

    plan = plan_published_run_cleanup(
        scoped_output_root=tmp_path,
        client_id="cliente-a",
        run_id="run-a",
        publication_confirmed=True,
        history_confirmed=True,
        compact_snapshot_confirmed=True,
        cloud_cleanup_ready=False,
    )

    assert plan.candidates == ()
    assert cloud_staging.is_dir()


def test_published_cleanup_requires_document_and_history_confirmation(tmp_path: Path) -> None:
    for publication, history in ((False, True), (True, False)):
        try:
            plan_published_run_cleanup(
                scoped_output_root=tmp_path,
                client_id="cliente-a",
                run_id="run-a",
                publication_confirmed=publication,
                history_confirmed=history,
            )
        except ValueError as exc:
            assert "confirmad" in str(exc).lower()
        else:
            raise AssertionError("A limpeza não pode ignorar as confirmações.")


def test_tag_datasets_follow_the_same_publication_and_history_cleanup_gates(
    tmp_path: Path,
) -> None:
    tag_dataset = (
        tmp_path / "report-datasets" / "cliente-a" / "run-a"
        / "2026-07" / "tags" / "tag-a" / "report-dataset.json"
    )
    tag_dataset.parent.mkdir(parents=True)
    tag_dataset.write_text("{}", encoding="utf-8")

    for publication, history in ((False, True), (True, False)):
        with pytest.raises(ValueError):
            plan_published_run_cleanup(
                scoped_output_root=tmp_path,
                client_id="cliente-a",
                run_id="run-a",
                publication_confirmed=publication,
                history_confirmed=history,
            )
        assert tag_dataset.is_file()

    plan = plan_published_run_cleanup(
        scoped_output_root=tmp_path,
        client_id="cliente-a",
        run_id="run-a",
        publication_confirmed=True,
        history_confirmed=True,
    )
    apply_cleanup_plan(scoped_output_root=tmp_path, candidates=plan.candidates)
    assert not tag_dataset.exists()


def test_failed_staging_is_retained_for_seven_days_then_becomes_eligible(
    tmp_path: Path,
) -> None:
    _run(tmp_path, "raw", "run-failed-new", 6)
    _run(tmp_path, "raw", "run-failed-old", 8)

    plan = plan_tiered_retention(
        scoped_output_root=tmp_path,
        policy=POLICY,
        run_status={"run-failed-new": "FAILED", "run-failed-old": "FAILED"},
        now=NOW,
    )

    assert [(item.category, item.run_id) for item in plan.candidates] == [
        ("raw", "run-failed-old")
    ]


def test_docx_directory_requires_explicit_analyst_deletion(tmp_path: Path) -> None:
    _run(tmp_path, "reports", "run-old-report", 900)

    plan = plan_tiered_retention(
        scoped_output_root=tmp_path,
        policy=POLICY,
        run_status={"run-old-report": "COMPLETE"},
        history_confirmed_run_ids={"run-old-report"},
        now=NOW,
    )

    assert not plan.candidates
    assert plan.skipped[0].reason == "DOCUMENTS_REQUIRE_EXPLICIT_DELETE"


def test_orchestration_logs_expire_after_configured_horizon(tmp_path: Path) -> None:
    old = _run(tmp_path, "orchestration", "old-log-run", 91)
    _run(tmp_path, "orchestration", "recent-log-run", 89)

    plan = plan_orchestration_log_cleanup(
        scoped_output_root=tmp_path,
        retention_days=90,
        now=NOW,
    )
    result = apply_cleanup_plan(
        scoped_output_root=tmp_path,
        candidates=plan.candidates,
    )

    assert result.removed == (old,)
    assert not old.exists()
