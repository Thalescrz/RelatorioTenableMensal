"""Application contracts for durable report component attempts."""

from __future__ import annotations

from typing import Protocol

from tenable_reports.domain.report_components import ComponentAttempt


class ReportComponentRepository(Protocol):
    def create_attempt(self, attempt: ComponentAttempt) -> ComponentAttempt: ...

    def latest_attempts(
        self,
        *,
        source_run_id: str,
        client_id: str,
    ) -> tuple[ComponentAttempt, ...]: ...


__all__ = ["ReportComponentRepository"]
