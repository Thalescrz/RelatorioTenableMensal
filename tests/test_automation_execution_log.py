from __future__ import annotations

from datetime import UTC, datetime

from tenable_reports.application.automation_execution_log import (
    append_execution_log,
    build_execution_log_payload,
)
from tenable_reports.cli import build_parser


def test_automation_log_preserves_result_without_operational_identifiers() -> None:
    payload = build_execution_log_payload(
        started_at=datetime(2026, 11, 1, 3, 5, tzinfo=UTC),
        finished_at=datetime(2026, 11, 1, 3, 7, tzinfo=UTC),
        exit_code=1,
        stdout=(
            '{"status":"COMPLETE_WITH_FAILURES","competence":"2026-10",'
            '"root_batch_id":"batch-sensitive","snapshot":{"counts":{"failed":2},'
            '"client_id":"client-sensitive"}}\n'
            "texto livre com Cliente Sensível\n"
        ),
        stderr="erro: api_key=secret-value em C:\\dados\\cliente\n",
    )

    rendered = str(payload)
    assert payload["exit_code"] == 1
    assert payload["stdout"][0]["status"] == "COMPLETE_WITH_FAILURES"
    assert payload["stdout"][0]["competence"] == "2026-10"
    assert payload["stdout"][0]["snapshot"]["counts"] == {"failed": 2}
    assert payload["stdout"][1] == "[NON_STRUCTURED_OUTPUT_REDACTED]"
    for forbidden in (
        "batch-sensitive",
        "client-sensitive",
        "Cliente Sensível",
        "secret-value",
        "C:\\dados\\cliente",
    ):
        assert forbidden not in rendered


def test_automation_log_is_jsonl_and_monthly_parser_accepts_explicit_office(
    tmp_path,
) -> None:
    parser = build_parser()
    parsed = parser.parse_args(
        ["run-monthly-batch", "--libreoffice", "C:/runtime/soffice.exe"]
    )
    assert parsed.libreoffice == "C:/runtime/soffice.exe"

    log_path = tmp_path / "monthly-automation.jsonl"
    payload = build_execution_log_payload(
        started_at=datetime(2026, 11, 1, 3, 5, tzinfo=UTC),
        finished_at=datetime(2026, 11, 1, 3, 7, tzinfo=UTC),
        exit_code=0,
        stdout='{"status":"COMPLETE","competence":"2026-10"}',
        stderr="",
    )
    append_execution_log(log_path, payload)

    assert log_path.read_text(encoding="utf-8").count("\n") == 1
    assert '"competence":"2026-10"' in log_path.read_text(encoding="utf-8")
