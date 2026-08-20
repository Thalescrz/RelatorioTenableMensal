from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class FailureCode(StrEnum):
    TENABLE_RATE_LIMIT = "TENABLE_RATE_LIMIT"
    TENABLE_TEMPORARY = "TENABLE_TEMPORARY"
    TENABLE_AUTH_INVALID = "TENABLE_AUTH_INVALID"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DISK_INSUFFICIENT = "DISK_INSUFFICIENT"
    DOCX_INVALID = "DOCX_INVALID"
    PROFILE_INVALID = "PROFILE_INVALID"
    UNEXPECTED = "UNEXPECTED"


RETRYABLE_CODES = frozenset({
    FailureCode.TENABLE_RATE_LIMIT,
    FailureCode.TENABLE_TEMPORARY,
    FailureCode.DATABASE_UNAVAILABLE,
    FailureCode.DISK_INSUFFICIENT,
})


_SECRET_PATTERN = re.compile(
    r"(?i)((?:tenable_(?:access|secret)|api[_-]?(?:key|secret)|token|password)"
    r"\s*[=:]\s*)[^\s,;]+"
)


def sanitize_failure_message(value: Any) -> str:
    text = _SECRET_PATTERN.sub(r"\1[REDACTED]", str(value or "")).strip()
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
    for code in FailureCode:
        if code.value in upper:
            return code
    if "429" in upper or "RATE LIMIT" in upper or "TOO MANY REQUESTS" in upper:
        return FailureCode.TENABLE_RATE_LIMIT
    if any(token in upper for token in ("401", "403", "CREDENTIAL", "UNAUTHORIZED")):
        return FailureCode.TENABLE_AUTH_INVALID
    if any(token in upper for token in ("TIMEOUT", "TIMED OUT", "502", "503", "504")):
        return FailureCode.TENABLE_TEMPORARY
    if any(token in upper for token in ("POSTGRES", "DATABASE", "CONNECTION REFUSED")):
        return FailureCode.DATABASE_UNAVAILABLE
    if "DOCX" in upper or "BADZIPFILE" in upper:
        return FailureCode.DOCX_INVALID
    if "PROFILE" in upper or "PERFIL" in upper:
        return FailureCode.PROFILE_INVALID
    return FailureCode.UNEXPECTED


def classify_failure(value: Any) -> OperationalFailure:
    if isinstance(value, OperationalFailure):
        return value
    if isinstance(value, Mapping):
        raw_code = str(value.get("error_code") or "")
        try:
            code = FailureCode(raw_code)
        except ValueError:
            code = _code_from_text(
                f"{raw_code} {value.get('message') or value.get('error') or ''}"
            )
        message = sanitize_failure_message(
            value.get("message") or value.get("error") or raw_code
        )
    else:
        message = sanitize_failure_message(value)
        code = _code_from_text(message)
    return OperationalFailure(
        code=code,
        message=message,
        retryable=code in RETRYABLE_CODES,
    )
