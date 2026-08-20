from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tenable_reports.domain.history import HistorySnapshot
from tenable_reports.domain.report_reference import (
    ReportCandidate,
    ReportReferenceKey,
    main_eligibility,
    reference_key_for_candidate,
)


class ReportRegistryError(RuntimeError):
    """Base error for report registry invariants."""


class ReportNotEligible(ReportRegistryError):
    pass


class IncompatibleReference(ReportRegistryError):
    pass


class MainDeletionRequiresDecision(ReportRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class RegisteredReport:
    candidate: ReportCandidate
    snapshot: HistorySnapshot | None = None
    deleted_at: str | None = None
    deleted_by: str | None = None
    deletion_reason: str | None = None

    @property
    def run_id(self) -> str:
        return self.candidate.run_id

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(frozen=True, slots=True)
class MainReport:
    key: ReportReferenceKey
    run_id: str
    candidate: ReportCandidate
    snapshot: HistorySnapshot | None
    set_by: str
    set_reason: str
    set_at: str


@dataclass(frozen=True, slots=True)
class ReferenceEvent:
    event_type: str
    reference_key: str
    previous_run_id: str | None
    new_run_id: str | None
    actor: str
    reason: str
    event_at: str


class ReportRegistry(Protocol):
    def register_report(
        self,
        candidate: ReportCandidate,
        snapshot: HistorySnapshot | None = None,
    ) -> None: ...

    def get_report(self, run_id: str) -> RegisteredReport: ...

    def get_main(self, key: ReportReferenceKey) -> MainReport | None: ...

    def get_main_snapshot(self, key: ReportReferenceKey) -> HistorySnapshot | None: ...

    def list_main_snapshots_before(
        self,
        key: ReportReferenceKey,
    ) -> tuple[HistorySnapshot, ...]: ...

    def promote_main(
        self,
        key: ReportReferenceKey,
        run_id: str,
        *,
        actor: str,
        reason: str,
    ) -> MainReport: ...

    def auto_promote_if_empty(self, key: ReportReferenceKey, run_id: str) -> bool: ...

    def soft_delete(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        replacement_run_id: str | None = None,
        allow_gap: bool = False,
    ) -> None: ...

    def restore(self, run_id: str, *, actor: str, reason: str) -> None: ...

    def list_reports(
        self,
        *,
        client_id: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[RegisteredReport, ...]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _required(value: str, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} é obrigatório.")
    return normalized


class InMemoryReportRegistry(ReportRegistry):
    """Deterministic registry used offline and by unit tests.

    The PostgreSQL adapter implements the same invariants. Keeping this as a
    real implementation (instead of a mock) lets domain workflows be tested
    without a database server.
    """

    def __init__(self) -> None:
        self._reports: dict[str, RegisteredReport] = {}
        self._mains: dict[str, MainReport] = {}
        self._events: list[ReferenceEvent] = []

    def register_report(
        self,
        candidate: ReportCandidate,
        snapshot: HistorySnapshot | None = None,
    ) -> None:
        existing = self._reports.get(candidate.run_id)
        if existing and existing.candidate != candidate:
            raise ValueError(f"Run já registrado com dados diferentes: {candidate.run_id}")
        if snapshot is not None and snapshot.run_id != candidate.run_id:
            raise ValueError("O snapshot precisa pertencer ao mesmo run do relatório.")
        if existing:
            self._reports[candidate.run_id] = RegisteredReport(
                candidate=candidate,
                snapshot=snapshot if snapshot is not None else existing.snapshot,
                deleted_at=existing.deleted_at,
                deleted_by=existing.deleted_by,
                deletion_reason=existing.deletion_reason,
            )
            return
        self._reports[candidate.run_id] = RegisteredReport(candidate, snapshot)

    def get_report(self, run_id: str) -> RegisteredReport:
        try:
            return self._reports[run_id]
        except KeyError as exc:
            raise KeyError(f"Relatório não encontrado: {run_id}") from exc

    def get_main(self, key: ReportReferenceKey) -> MainReport | None:
        return self._mains.get(key.stable_key)

    def get_main_snapshot(self, key: ReportReferenceKey) -> HistorySnapshot | None:
        main = self.get_main(key)
        return main.snapshot if main else None

    def list_main_snapshots_before(
        self,
        key: ReportReferenceKey,
    ) -> tuple[HistorySnapshot, ...]:
        snapshots = [
            main.snapshot
            for main in self._mains.values()
            if main.snapshot is not None
            and main.key.client_id == key.client_id
            and main.key.tenant_id == key.tenant_id
            and main.key.kind == key.kind
            and main.key.period_mode == key.period_mode
            and main.key.timezone == key.timezone
            and main.key.scope_hash == key.scope_hash
            and main.key.metric_definition_version == key.metric_definition_version
            and main.key.period_key < key.period_key
        ]
        return tuple(sorted(snapshots, key=lambda item: (item.period_end_at, item.run_id)))

    def _validate_promotion(
        self,
        key: ReportReferenceKey,
        run_id: str,
    ) -> RegisteredReport:
        report = self.get_report(run_id)
        if report.deleted:
            raise ReportNotEligible("Relatório excluído não pode ser promovido.")
        actual_key = reference_key_for_candidate(report.candidate)
        if actual_key != key:
            raise IncompatibleReference(
                "O relatório não pertence à mesma referência, escopo ou versão métrica."
            )
        eligibility = main_eligibility(report.candidate)
        if not eligibility.eligible:
            raise ReportNotEligible(
                "Relatório inelegível para main: " + ", ".join(eligibility.reasons)
            )
        return report

    def _set_main(
        self,
        key: ReportReferenceKey,
        report: RegisteredReport,
        *,
        actor: str,
        reason: str,
        event_at: str | None = None,
    ) -> MainReport:
        previous = self.get_main(key)
        timestamp = event_at or _now()
        main = MainReport(
            key=key,
            run_id=report.run_id,
            candidate=report.candidate,
            snapshot=report.snapshot,
            set_by=actor,
            set_reason=reason,
            set_at=timestamp,
        )
        self._mains[key.stable_key] = main
        self._events.append(ReferenceEvent(
            event_type="MAIN_PROMOTED",
            reference_key=key.stable_key,
            previous_run_id=previous.run_id if previous else None,
            new_run_id=report.run_id,
            actor=actor,
            reason=reason,
            event_at=timestamp,
        ))
        return main

    def promote_main(
        self,
        key: ReportReferenceKey,
        run_id: str,
        *,
        actor: str,
        reason: str,
    ) -> MainReport:
        actor = _required(actor, "actor")
        reason = _required(reason, "reason")
        report = self._validate_promotion(key, run_id)
        return self._set_main(key, report, actor=actor, reason=reason)

    def auto_promote_if_empty(self, key: ReportReferenceKey, run_id: str) -> bool:
        if self.get_main(key) is not None:
            return False
        report = self._validate_promotion(key, run_id)
        self._set_main(
            key,
            report,
            actor="system",
            reason="FIRST_ELIGIBLE_REPORT",
        )
        return True

    def soft_delete(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        replacement_run_id: str | None = None,
        allow_gap: bool = False,
    ) -> None:
        actor = _required(actor, "actor")
        reason = _required(reason, "reason")
        report = self.get_report(run_id)
        if report.deleted:
            return
        key = reference_key_for_candidate(report.candidate)
        current = self.get_main(key)
        is_main = current is not None and current.run_id == run_id
        replacement: RegisteredReport | None = None
        if is_main:
            if replacement_run_id:
                if replacement_run_id == run_id:
                    raise MainDeletionRequiresDecision(
                        "A substituta precisa ser uma geração diferente."
                    )
                replacement = self._validate_promotion(key, replacement_run_id)
            elif not allow_gap:
                raise MainDeletionRequiresDecision(
                    "Escolha uma geração substituta ou confirme a lacuna histórica."
                )

        timestamp = _now()
        if replacement is not None:
            self._set_main(
                key,
                replacement,
                actor=actor,
                reason=f"REPLACEMENT_ON_DELETE: {reason}",
                event_at=timestamp,
            )
        elif is_main:
            del self._mains[key.stable_key]
            self._events.append(ReferenceEvent(
                event_type="MAIN_CLEARED",
                reference_key=key.stable_key,
                previous_run_id=run_id,
                new_run_id=None,
                actor=actor,
                reason=reason,
                event_at=timestamp,
            ))

        self._reports[run_id] = RegisteredReport(
            candidate=report.candidate,
            snapshot=report.snapshot,
            deleted_at=timestamp,
            deleted_by=actor,
            deletion_reason=reason,
        )
        self._events.append(ReferenceEvent(
            event_type="REPORT_SOFT_DELETED",
            reference_key=key.stable_key,
            previous_run_id=run_id,
            new_run_id=replacement.run_id if replacement else None,
            actor=actor,
            reason=reason,
            event_at=timestamp,
        ))

    def restore(self, run_id: str, *, actor: str, reason: str) -> None:
        actor = _required(actor, "actor")
        reason = _required(reason, "reason")
        report = self.get_report(run_id)
        if not report.deleted:
            return
        self._reports[run_id] = RegisteredReport(
            candidate=report.candidate,
            snapshot=report.snapshot,
        )
        key = reference_key_for_candidate(report.candidate)
        self._events.append(ReferenceEvent(
            event_type="REPORT_RESTORED",
            reference_key=key.stable_key,
            previous_run_id=None,
            new_run_id=run_id,
            actor=actor,
            reason=reason,
            event_at=_now(),
        ))

    def list_reports(
        self,
        *,
        client_id: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[RegisteredReport, ...]:
        reports = (
            report
            for report in self._reports.values()
            if (client_id is None or report.candidate.client_id == client_id)
            and (include_deleted or not report.deleted)
        )
        return tuple(sorted(reports, key=lambda item: item.run_id))

    def reference_events(self) -> tuple[ReferenceEvent, ...]:
        return tuple(self._events)
