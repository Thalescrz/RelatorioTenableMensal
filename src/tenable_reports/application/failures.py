from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from tenable_reports.infrastructure.tenable_vm.client import ExportTimeoutError


class FailureCode(StrEnum):
    TENABLE_RATE_LIMIT = "TENABLE_RATE_LIMIT"
    TENABLE_TEMPORARY = "TENABLE_TEMPORARY"
    TENABLE_AUTH_INVALID = "TENABLE_AUTH_INVALID"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DISK_INSUFFICIENT = "DISK_INSUFFICIENT"
    DOCX_INVALID = "DOCX_INVALID"
    PROFILE_INVALID = "PROFILE_INVALID"
    LOCAL_ARTIFACT_SCOPE_MISMATCH = "LOCAL_ARTIFACT_SCOPE_MISMATCH"
    CHECKPOINT_ARTIFACT_MISSING = "CHECKPOINT_ARTIFACT_MISSING"
    CHECKPOINT_COMPONENT_INCOMPLETE = "CHECKPOINT_COMPONENT_INCOMPLETE"
    LOCAL_FILESYSTEM_TRANSIENT = "LOCAL_FILESYSTEM_TRANSIENT"
    UNEXPECTED = "UNEXPECTED"


RETRYABLE_CODES = frozenset({
    FailureCode.TENABLE_RATE_LIMIT,
    FailureCode.TENABLE_TEMPORARY,
    FailureCode.DATABASE_UNAVAILABLE,
    FailureCode.DISK_INSUFFICIENT,
    FailureCode.CHECKPOINT_COMPONENT_INCOMPLETE,
    FailureCode.LOCAL_FILESYSTEM_TRANSIENT,
})


_SECRET_PATTERN = re.compile(
    r"(?i)((?:tenable_(?:access|secret)|api[_-]?(?:key|secret)|token|password)"
    r"\s*[=:]\s*)[^\s,;]+"
)


def sanitize_failure_message(value: Any) -> str:
    text = _SECRET_PATTERN.sub(r"\1[REDACTED]", str(value or "")).strip()
    normalized = text.upper().replace("�", "")
    if "TOO MANY CONNECTIONS" in normalized or "MUITAS CONEX" in normalized:
        return "PostgreSQL sem conexões disponíveis; a operação será retentada."
    return text[-4000:] or "Falha operacional sem mensagem detalhada."


@dataclass(frozen=True, slots=True)
class OperationalFailure(Exception):
    code: FailureCode
    message: str
    retryable: bool

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "failed",
            "error_code": self.code.value,
            "retryable": self.retryable,
            "message": self.message,
        }


def _code_from_text(value: str) -> FailureCode:
    upper = value.upper()
    if "EXPORT" in upper and "TERMINOU COM ESTADO CANCEL" in upper:
        return FailureCode.TENABLE_TEMPORARY
    if "EXPORT" in upper and "SEM PROGRESSO" in upper:
        return FailureCode.TENABLE_TEMPORARY
    if (
        "CHECKPOINT" in upper
        and "CLOUD" in upper
        and ("NAO ESTA COMPLETO" in upper or "NÃO ESTÁ COMPLETO" in upper)
    ):
        return FailureCode.CHECKPOINT_COMPONENT_INCOMPLETE
    for code in FailureCode:
        if code.value in upper:
            return code
    if "429" in upper or "RATE LIMIT" in upper or "TOO MANY REQUESTS" in upper:
        return FailureCode.TENABLE_RATE_LIMIT
    if any(token in upper for token in ("401", "403", "CREDENTIAL", "UNAUTHORIZED")):
        return FailureCode.TENABLE_AUTH_INVALID
    if (
        "CHECKPOINT_ARTIFACT_MISSING" in upper
        or ("CHECKPOINT" in upper and ("AUSENTE" in upper or "INVALID" in upper))
    ):
        return FailureCode.CHECKPOINT_ARTIFACT_MISSING
    if (
        "NAO FOI POSSIVEL LER O ARTEFATO" in upper
        and "MANIFEST.JSON" in upper
    ):
        return FailureCode.LOCAL_ARTIFACT_SCOPE_MISMATCH
    if (
        ("WINERROR 5" in upper or "ACCESS IS DENIED" in upper)
        and "EXPORT-STATE.JSON" in upper
    ):
        return FailureCode.LOCAL_FILESYSTEM_TRANSIENT
    if (
        re.search(r"TEMPO MAXIMO(?: TOTAL)? EXCEDIDO", upper)
        or any(token in upper for token in (
            "TIMEOUT", "TIMED OUT", "FALHA DE TRANSPORTE",
            "502", "503", "504",
        ))
    ):
        return FailureCode.TENABLE_TEMPORARY
    if any(token in upper for token in ("POSTGRES", "DATABASE", "CONNECTION REFUSED")):
        return FailureCode.DATABASE_UNAVAILABLE
    if "DOCX" in upper or "BADZIPFILE" in upper:
        return FailureCode.DOCX_INVALID
    if "PROFILE" in upper or "PERFIL" in upper:
        return FailureCode.PROFILE_INVALID
    return FailureCode.UNEXPECTED


def classify_failure(value: Any) -> OperationalFailure:
    structured_code = str(getattr(value, "failure_code", "") or "")
    if structured_code:
        try:
            code = FailureCode(structured_code)
        except ValueError:
            code = _code_from_text(structured_code)
        return OperationalFailure(
            code=code,
            message=sanitize_failure_message(value),
            retryable=bool(getattr(value, "retryable", code in RETRYABLE_CODES)),
        )
    if isinstance(value, ExportTimeoutError):
        return OperationalFailure(
            code=FailureCode.TENABLE_TEMPORARY,
            message=sanitize_failure_message(value),
            retryable=True,
        )
    if isinstance(value, OperationalFailure):
        return value
    if isinstance(value, Mapping):
        raw_code = str(value.get("error_code") or "")
        message = sanitize_failure_message(
            value.get("message") or value.get("error") or raw_code
        )
        try:
            code = FailureCode(raw_code)
        except ValueError:
            code = _code_from_text(f"{raw_code} {message}")
        if code is FailureCode.UNEXPECTED:
            inferred_code = _code_from_text(message)
            if inferred_code is not FailureCode.UNEXPECTED:
                code = inferred_code
    else:
        message = sanitize_failure_message(value)
        code = _code_from_text(message)
    return OperationalFailure(
        code=code,
        message=message,
        retryable=code in RETRYABLE_CODES,
    )
