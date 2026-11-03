from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tenable_reports.application.was_owasp_2025 import (
    collect_exclusive_owasp_2025_snapshot,
)
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.reporting import PeriodMode, ReportingPeriod
from tenable_reports.infrastructure.tenable_vm.client import ApiError
from tenable_reports.infrastructure.tenable_was.owasp_analysis import (
    Owasp2025PluginGroup,
)


def profile() -> ClientProfile:
    return ClientProfile.from_dict({
        "schema_version": 1,
        "client_id": "cliente-fixture",
        "display_name": "Cliente Fixture",
        "tenant_id": "tenant-fixture",
        "scope": {
            "was": {
                "enabled": True,
                "owasp_2025_mode": "console_exclusive",
            }
        },
    })


def period() -> ReportingPeriod:
    return ReportingPeriod(
        start_at=datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc),
        timezone="America/Fortaleza",
        mode=PeriodMode.PREVIOUS_CALENDAR_MONTH,
        reference_at=datetime(2026, 11, 1, 3, 5, tzinfo=timezone.utc),
    )


class FakeAnalysisClient:
    def __init__(self, *, fail_category: str | None = None) -> None:
        self.fail_category = fail_category
        self.calls: list[str] = []

    def list_category_plugins(
        self,
        *,
        category: str,
        start_at: datetime,
        end_at: datetime,
        page_size: int = 200,
    ) -> tuple[Owasp2025PluginGroup, ...]:
        self.calls.append(category)
        if category == self.fail_category:
            raise ApiError("falha externa sanitizada", status_code=502)
        index = int(category[1:])
        return (
            Owasp2025PluginGroup(
                category=category,
                plugin_id=110000 + index,
                name=f"Plugin fixture {index}",
                family="Família fixture",
                severity="HIGH" if index == 1 else "MEDIUM",
                vpr_score=9.1 - (index / 10),
                instances=index,
            ),
        )


def test_exclusive_collection_persists_ten_checkpoints_and_complete_snapshot(
    tmp_path: Path,
) -> None:
    client = FakeAnalysisClient()

    attempt = collect_exclusive_owasp_2025_snapshot(
        client=client,
        profile=profile(),
        period=period(),
        output_root=tmp_path,
        run_id="run-fixture",
    )

    assert attempt.status == "COMPLETE"
    assert attempt.failure is None
    assert attempt.completed_categories == tuple(f"A{index:02d}" for index in range(1, 11))
    assert client.calls == list(attempt.completed_categories)
    assert attempt.snapshot_path == (
        tmp_path
        / "snapshots"
        / "cliente-fixture"
        / "run-fixture"
        / "tenable_was_owasp_2025.snapshot.json"
    )
    snapshot = json.loads(attempt.snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["mode"] == "console_exclusive"
    assert snapshot["source"] == "console_analysis_exclusive"
    assert snapshot["coverage"] == "COMPLETE"
    assert snapshot["period"]["interval"] == "[start_at, end_at)"
    assert len(snapshot["contract_sha256"]) == 64
    assert snapshot["category_counts"] == {
        f"A{index:02d}": 1 for index in range(1, 11)
    }
    assert snapshot["categories"]["A01"][0]["plugin_id"] == 110001
    assert "asset" not in json.dumps(snapshot).lower()
    checkpoints = sorted(
        (
            tmp_path
            / "recovery"
            / "cliente-fixture"
            / "run-fixture"
            / "was-owasp-2025"
        ).glob("A*.json")
    )
    assert len(checkpoints) == 10


def test_exclusive_collection_resumes_only_missing_or_incompatible_categories(
    tmp_path: Path,
) -> None:
    first = FakeAnalysisClient(fail_category="A05")

    failed = collect_exclusive_owasp_2025_snapshot(
        client=first,
        profile=profile(),
        period=period(),
        output_root=tmp_path,
        run_id="run-resume",
    )

    assert failed.status == "UNAVAILABLE"
    assert failed.snapshot_path is None
    assert failed.completed_categories == ("A01", "A02", "A03", "A04")
    assert failed.failure is not None
    assert failed.failure.code == "WAS_OWASP_2025_EXCLUSIVE_UNAVAILABLE"
    assert failed.failure.retryable is True
    assert failed.failure.failed_category == "A05"
    final_snapshot = (
        tmp_path
        / "snapshots"
        / "cliente-fixture"
        / "run-resume"
        / "tenable_was_owasp_2025.snapshot.json"
    )
    assert not final_snapshot.exists()

    incompatible = (
        tmp_path
        / "recovery"
        / "cliente-fixture"
        / "run-resume"
        / "was-owasp-2025"
        / "A02.json"
    )
    payload = json.loads(incompatible.read_text(encoding="utf-8"))
    payload["client_id"] = "outro-cliente"
    incompatible.write_text(json.dumps(payload), encoding="utf-8")

    second = FakeAnalysisClient()
    resumed = collect_exclusive_owasp_2025_snapshot(
        client=second,
        profile=profile(),
        period=period(),
        output_root=tmp_path,
        run_id="run-resume",
    )

    assert resumed.status == "COMPLETE"
    assert second.calls == ["A02", "A05", "A06", "A07", "A08", "A09", "A10"]
    assert final_snapshot.is_file()


def test_exclusive_collection_copies_valid_checkpoints_into_recovery_run(
    tmp_path: Path,
) -> None:
    first = FakeAnalysisClient(fail_category="A05")
    failed = collect_exclusive_owasp_2025_snapshot(
        client=first,
        profile=profile(),
        period=period(),
        output_root=tmp_path,
        run_id="run-original",
    )
    assert failed.completed_categories == ("A01", "A02", "A03", "A04")

    second = FakeAnalysisClient()
    resumed = collect_exclusive_owasp_2025_snapshot(
        client=second,
        profile=profile(),
        period=period(),
        output_root=tmp_path,
        run_id="run-original-was-recovery",
        resume_run_id="run-original",
    )

    assert resumed.status == "COMPLETE"
    assert second.calls == ["A05", "A06", "A07", "A08", "A09", "A10"]
    target_root = (
        tmp_path
        / "recovery"
        / "cliente-fixture"
        / "run-original-was-recovery"
        / "was-owasp-2025"
    )
    assert len(tuple(target_root.glob("A*.json"))) == 10
    target_a01 = json.loads((target_root / "A01.json").read_text(encoding="utf-8"))
    assert target_a01["run_id"] == "run-original-was-recovery"
