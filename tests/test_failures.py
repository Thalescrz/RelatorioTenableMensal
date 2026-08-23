from __future__ import annotations

from tenable_reports.application.failures import FailureCode, classify_failure
from tenable_reports.infrastructure.tenable_vm.client import ExportTimeoutError


def test_rate_limit_is_retryable_but_invalid_credentials_are_not() -> None:
    rate_limit = classify_failure({"error_code": "TENABLE_RATE_LIMIT", "message": "429"})
    invalid_auth = classify_failure({
        "error_code": "TENABLE_AUTH_INVALID",
        "message": "invalid credentials",
    })

    assert rate_limit.code is FailureCode.TENABLE_RATE_LIMIT
    assert rate_limit.retryable is True
    assert invalid_auth.code is FailureCode.TENABLE_AUTH_INVALID
    assert invalid_auth.retryable is False


def test_failure_classification_does_not_echo_secrets() -> None:
    failure = classify_failure(
        "TENABLE_SECRET=super-secret token=abc123 returned HTTP 429"
    )
    assert failure.code is FailureCode.TENABLE_RATE_LIMIT
    assert "super-secret" not in failure.message
    assert "abc123" not in failure.message


def test_portuguese_export_timeout_is_a_retryable_tenable_failure() -> None:
    failure = classify_failure(
        ExportTimeoutError("Tempo maximo excedido aguardando o export VM.")
    )

    assert failure.code is FailureCode.TENABLE_TEMPORARY
    assert failure.retryable is True


def test_remote_cancelled_export_is_a_retryable_tenable_failure() -> None:
    failure = classify_failure(
        "Export VM terminou com estado cancelled."
    )

    assert failure.code is FailureCode.TENABLE_TEMPORARY
    assert failure.retryable is True
