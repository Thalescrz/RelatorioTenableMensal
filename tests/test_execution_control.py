from __future__ import annotations

import json

import pytest

from tenable_reports.application.execution_control import FileExecutionControl
from tenable_reports.domain.execution_control import ExecutionInterruptedError


def test_missing_control_file_does_not_request_stop(tmp_path) -> None:
    control = FileExecutionControl(tmp_path / "job-control.json")

    assert control.is_stop_requested() is False
    control.raise_if_stop_requested()


def test_stop_request_is_atomic_sanitized_and_survives_recreation(tmp_path) -> None:
    path = tmp_path / "job-control.json"
    control = FileExecutionControl(path)

    control.request_stop(reason="Parada solicitada pelo analista.")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["stop_requested"] is True
    assert payload["reason"] == "Parada solicitada pelo analista."
    assert payload["requested_at"].endswith("Z")
    assert not path.with_suffix(".json.tmp").exists()
    assert FileExecutionControl(path).is_stop_requested() is True


def test_raise_if_stop_requested_uses_specific_exception_and_keeps_file(tmp_path) -> None:
    path = tmp_path / "job-control.json"
    control = FileExecutionControl(path)
    control.request_stop(reason="fixture")

    with pytest.raises(ExecutionInterruptedError, match="interrompida"):
        FileExecutionControl(path).raise_if_stop_requested()

    assert path.is_file()
