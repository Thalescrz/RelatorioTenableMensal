from __future__ import annotations

from pathlib import Path

from tenable_reports.application.monthly_cutoff_repair import (
    MONTHLY_CUTOFF_MODE,
    is_monthly_cutoff_interval,
    monthly_competence_document_path,
    needs_monthly_cutoff_repair,
    rewrite_monthly_cutoff_checkpoint,
    rewrite_monthly_cutoff_manifest,
)


def test_monthly_cutoff_interval_requires_first_and_last_day() -> None:
    assert is_monthly_cutoff_interval(
        period_id="2026-09",
        start_at="2026-09-01T03:00:00Z",
        end_at="2026-10-01T00:26:07Z",
        timezone_name="America/Fortaleza",
    )
    assert not is_monthly_cutoff_interval(
        period_id="2026-09",
        start_at="2026-09-15T03:00:00Z",
        end_at="2026-10-01T00:26:07Z",
        timezone_name="America/Fortaleza",
    )


def test_monthly_cutoff_interval_accepts_completed_full_month() -> None:
    assert is_monthly_cutoff_interval(
        period_id="2026-09",
        start_at="2026-09-01T03:00:00Z",
        end_at="2026-10-01T03:00:00Z",
        timezone_name="America/Fortaleza",
    )


def test_repair_is_needed_when_period_id_is_monthly_but_mode_is_stale() -> None:
    assert needs_monthly_cutoff_repair(
        stored_period_id="2026-09",
        stored_period_mode="EXPLICIT_RANGE",
        manifest_period={"period_id": "2026-09", "mode": "EXPLICIT_RANGE"},
        target_period_id="2026-09",
    )
    assert not needs_monthly_cutoff_repair(
        stored_period_id="2026-09",
        stored_period_mode=MONTHLY_CUTOFF_MODE,
        manifest_period={
            "period_id": "2026-09",
            "mode": MONTHLY_CUTOFF_MODE,
        },
        target_period_id="2026-09",
    )


def test_document_path_replaces_exact_range_with_month_competence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "[CLIENTE] Relatório Tenable 01SET26-30SET26.docx"

    result = monthly_competence_document_path(source, period_id="2026-09")

    assert result.name == "[CLIENTE] Relatório Tenable SET26.docx"


def test_manifest_rewrite_preserves_effective_cutoff_and_relabels_documents(
    tmp_path: Path,
) -> None:
    source = tmp_path / "[CLIENTE] Relatório Tenable 01SET26-30SET26.docx"
    target = tmp_path / "[CLIENTE] Relatório Tenable SET26.docx"
    manifest = {
        "run_id": "run-sanitized",
        "period": {
            "period_id": "20260901T000000-20260930T212607",
            "mode": "EXPLICIT_RANGE",
            "timezone": "America/Fortaleza",
            "start_at": "2026-09-01T03:00:00Z",
            "end_at": "2026-10-01T00:26:07Z",
            "reference_at": "2026-10-01T00:26:07Z",
        },
        "documents": [{"path": str(source), "sha256": "a" * 64}],
    }

    result = rewrite_monthly_cutoff_manifest(
        manifest,
        period_id="2026-09",
        document_paths={source.resolve(): target.resolve()},
        repaired_at="2026-10-01T09:00:00Z",
    )

    assert result["period"]["period_id"] == "2026-09"
    assert result["period"]["mode"] == MONTHLY_CUTOFF_MODE
    assert result["period"]["end_at"] == "2026-10-01T00:26:07Z"
    assert result["documents"][0]["path"] == str(target.resolve())
    assert result["competence_repair"]["period_id"] == "2026-09"


def test_checkpoint_rewrite_preserves_window_and_updates_only_period_identity() -> None:
    checkpoint = {
        "schema_version": 1,
        "run_id": "run-sanitized",
        "mode": "manual",
        "period": {
            "period_id": "20260901T000000-20260930T203418",
            "mode": "EXPLICIT_RANGE",
            "timezone": "America/Fortaleza",
            "start_at": "2026-09-01T03:00:00Z",
            "end_at": "2026-09-30T23:34:18.971893Z",
            "reference_at": "2026-09-30T23:34:18.971893Z",
        },
        "query_fingerprint": "a" * 64,
    }

    result = rewrite_monthly_cutoff_checkpoint(
        checkpoint,
        period_id="2026-09",
    )

    assert result["period"] == {
        **checkpoint["period"],
        "period_id": "2026-09",
        "mode": MONTHLY_CUTOFF_MODE,
    }
    assert result["mode"] == "manual"
    assert result["query_fingerprint"] == "a" * 64
    assert checkpoint["period"]["mode"] == "EXPLICIT_RANGE"


def test_checkpoint_rewrite_rejects_window_outside_target_competence() -> None:
    checkpoint = {
        "period": {
            "period_id": "20260915T000000-20260930T203418",
            "mode": "EXPLICIT_RANGE",
            "timezone": "America/Fortaleza",
            "start_at": "2026-09-15T03:00:00Z",
            "end_at": "2026-09-30T23:34:18.971893Z",
        }
    }

    try:
        rewrite_monthly_cutoff_checkpoint(checkpoint, period_id="2026-09")
    except ValueError as exc:
        assert "competência" in str(exc)
    else:
        raise AssertionError("Checkpoint fora da competência deveria ser recusado.")
