"""Durable file-backed control plane for one local client execution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from tenable_reports.application.publishing import write_json_atomic
from tenable_reports.domain.execution_control import ExecutionInterruptedError


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class FileExecutionControl:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def request_stop(self, *, reason: str) -> None:
        message = str(reason or "").strip()[:500]
        write_json_atomic(
            self.path,
            {
                "schema_version": 1,
                "stop_requested": True,
                "reason": message,
                "requested_at": _now(),
            },
        )

    def state(self) -> Mapping[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return dict(payload) if isinstance(payload, Mapping) else {}

    def is_stop_requested(self) -> bool:
        return self.state().get("stop_requested") is True

    def raise_if_stop_requested(
        self,
        *,
        export_uuid: str | None = None,
        checkpoint: str | None = None,
    ) -> None:
        if not self.is_stop_requested():
            return
        suffix = (
            f" O export {export_uuid} foi preservado para retomada."
            if export_uuid
            else ""
        )
        raise ExecutionInterruptedError(
            f"Execucao interrompida por solicitacao local.{suffix}",
            export_uuid=export_uuid,
            checkpoint=checkpoint,
        )


__all__ = ["FileExecutionControl"]
