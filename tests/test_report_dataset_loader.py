from __future__ import annotations

import json
from pathlib import Path

import pytest

from tenable_reports.application import report_dataset as report_dataset_module
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.reporting import previous_calendar_month


def _period():
    return previous_calendar_month(
        reference_at="2026-09-12T10:00:00-03:00",
        timezone_name="America/Fortaleza",
    )


def _write_published_dataset(
    path: Path,
    *,
    run_id: str = "run-a",
    metric_definition_version: str = "report-definition-v1.2",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "metric_definition_version": metric_definition_version,
        "client_id": "client-a",
        "run_id": run_id,
        "execution_type": "MANUAL",
        "period": _period().to_dict(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_load_published_report_dataset_validates_and_returns_existing_payload(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "report-dataset.json"
    expected = _write_published_dataset(dataset_path)

    loaded = report_dataset_module.load_published_report_dataset(
        dataset_path,
        client_id="client-a",
        run_id="run-a",
        period=_period(),
    )

    assert loaded == expected


@pytest.mark.parametrize(
    ("run_id", "metric_definition_version"),
    (
        ("run-b", "report-definition-v1.2"),
        ("run-a", "report-definition-v9.9"),
    ),
)
def test_load_published_report_dataset_rejects_wrong_run_or_metric_definition(
    tmp_path: Path,
    run_id: str,
    metric_definition_version: str,
) -> None:
    dataset_path = tmp_path / "report-dataset.json"
    _write_published_dataset(
        dataset_path,
        run_id=run_id,
        metric_definition_version=metric_definition_version,
    )

    with pytest.raises(ValueError):
        report_dataset_module.load_published_report_dataset(
            dataset_path,
            client_id="client-a",
            run_id="run-a",
            period=_period(),
        )


def test_load_inputs_include_was_false_ignores_preserved_was_artifacts(
    tmp_path: Path,
) -> None:
    profile = ClientProfile.from_dict({
        "schema_version": 1,
        "client_id": "client-a",
        "display_name": "Cliente A",
        "tenant_id": "tenant-a",
        "scope": {
            "was": {
                "enabled": True,
                "owasp_2025_mode": "console_exclusive",
            }
        },
    })
    run_id = "run-a"
    normalized = tmp_path / "normalized" / profile.client_id / run_id
    normalized.mkdir(parents=True)
    (normalized / "manifest.json").write_text(
        json.dumps({"client_id": profile.client_id, "run_id": run_id}),
        encoding="utf-8",
    )
    (normalized / "assets.jsonl").write_text("", encoding="utf-8")
    (normalized / "findings.jsonl").write_text("", encoding="utf-8")
    (normalized / "was-findings.jsonl").write_text("{not-json}\n", encoding="utf-8")
    snapshots = tmp_path / "snapshots" / profile.client_id / run_id
    snapshots.mkdir(parents=True)
    completed = "2026-11-01T03:05:00Z"
    (snapshots / "tenable_vm_assets_v2.snapshot.json").write_text(
        json.dumps({"completed_at": completed}), encoding="utf-8"
    )
    (snapshots / "tenable_vm_vulnerabilities.snapshot.json").write_text(
        json.dumps({"completed_at": completed, "query": {}}), encoding="utf-8"
    )
    (snapshots / "tenable_was_findings.snapshot.json").write_text(
        json.dumps({"completed_at": completed}), encoding="utf-8"
    )
    (snapshots / "tenable_was_owasp_2025.snapshot.json").write_text(
        json.dumps({"coverage": "COMPLETE", "categories": {"A01": [{"plugin_id": 9}]}}),
        encoding="utf-8",
    )

    inputs = report_dataset_module.load_report_dataset_inputs(
        profile=profile,
        run_id=run_id,
        output_root=tmp_path,
        include_was=False,
    )

    assert inputs.was_findings == ()
    assert inputs.was_snapshot is None
    assert inputs.was_owasp_snapshot is None
