"""Registro sanitizado de execuções automáticas do orquestrador mensal."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


REDACTED = "[REDACTED]"
NON_STRUCTURED = "[NON_STRUCTURED_OUTPUT_REDACTED]"
_SAFE_TEXT_KEYS = frozenset(
    {
        "status",
        "competence",
        "error_code",
        "phase",
        "component",
        "period_mode",
    }
)
_SENSITIVE_KEY_PARTS = (
    "id",
    "uuid",
    "client",
    "tenant",
    "name",
    "email",
    "host",
    "ip",
    "path",
    "command",
    "message",
    "detail",
    "secret",
    "token",
    "password",
    "key",
)


def _sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_json(value: Any, *, key: str = "") -> Any:
    if _sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(nested_key): _sanitize_json(nested, key=str(nested_key))
            for nested_key, nested in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        return value if key.casefold() in _SAFE_TEXT_KEYS else REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return REDACTED


def _sanitize_stream(value: str) -> list[Any]:
    sanitized: list[Any] = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            sanitized.append(NON_STRUCTURED)
        else:
            sanitized.append(_sanitize_json(parsed))
    return sanitized


def build_execution_log_payload(
    *,
    started_at: datetime,
    finished_at: datetime,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    """Constrói uma linha auditável sem texto livre ou identificadores reais."""

    return {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": max(
            0.0,
            round((finished_at - started_at).total_seconds(), 3),
        ),
        "exit_code": int(exit_code),
        "stdout": _sanitize_stream(stdout),
        "stderr": _sanitize_stream(stderr),
    }


def append_execution_log(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with destination.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
        handle.write("\n")


__all__ = ["append_execution_log", "build_execution_log_payload"]
