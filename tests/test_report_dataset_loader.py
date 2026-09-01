from __future__ import annotations

import json
from pathlib import Path

import pytest

from tenable_reports.application import report_dataset as report_dataset_module
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
