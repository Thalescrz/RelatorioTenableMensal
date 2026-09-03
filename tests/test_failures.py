from __future__ import annotations

from tenable_reports.application.failures import FailureCode, classify_failure
from tenable_reports.infrastructure.tenable_vm.client import ExportTimeoutError


def test_observed_failure_messages_keep_actionable_classification() -> None:
    cases = (
        ("Tempo maximo excedido na fila do export VM.", FailureCode.TENABLE_TEMPORARY, True),
        ("endpoint=/assets/v2/export status=401", FailureCode.TENABLE_AUTH_INVALID, False),
        ("Falha de transporte ao acessar a Tenable.", FailureCode.TENABLE_TEMPORARY, True),
        ("Nao foi possivel ler o artefato: C:/dados/normalized/cliente/manifest.json", FailureCode.LOCAL_ARTIFACT_SCOPE_MISMATCH, False),
        ("CHECKPOINT_ARTIFACT_MISSING: dependencia declarada ausente", FailureCode.CHECKPOINT_ARTIFACT_MISSING, False),
        ("[WinError 5] Access is denied: export-state.json", FailureCode.LOCAL_FILESYSTEM_TRANSIENT, True),
    )
    for message, expected_code, expected_retryable in cases:
        failure = classify_failure(message)
        assert failure.code is expected_code
        assert failure.retryable is expected_retryable


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

def test_cloud_rate_limit_error_uses_structured_retryability() -> None:
    import importlib

    cloud = importlib.import_module(
        "tenable_reports.infrastructure.tenable_cloud.client"
    )
    failure = classify_failure(
        cloud.CloudRateLimitError("Cloud temporariamente limitada.", status_code=429)
    )

    assert failure.code is FailureCode.TENABLE_RATE_LIMIT
    assert failure.retryable is True


def test_postgresql_connection_exhaustion_is_clean_and_retryable() -> None:
    failure = classify_failure(
        'psycopg.OperationalError: FATAL: muitas conex�es para role "app"'
    )

    assert failure.code is FailureCode.DATABASE_UNAVAILABLE
    assert failure.retryable is True
    assert failure.message == (
        "PostgreSQL sem conexões disponíveis; a operação será retentada."
    )
    assert "�" not in failure.message
