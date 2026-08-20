from __future__ import annotations

from tenable_reports.application.failures import FailureCode, classify_failure


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
