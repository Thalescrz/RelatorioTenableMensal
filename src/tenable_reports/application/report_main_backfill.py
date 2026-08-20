from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from tenable_reports.domain.report_reference import (
    ReportCandidate,
    ReportReferenceKey,
    main_eligibility,
    reference_key_for_candidate,
)


@dataclass(frozen=True, slots=True)
class BackfillAlert:
    code: str
    reference_key: str
    run_ids: tuple[str, ...]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reference_key": self.reference_key,
            "run_ids": list(self.run_ids),
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class InvalidBackfillCandidate:
    run_id: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "reasons": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class MainBackfillPlan:
    promotions: tuple[tuple[ReportReferenceKey, str], ...]
    alerts: tuple[BackfillAlert, ...]
    invalid: tuple[InvalidBackfillCandidate, ...]
    already_selected_run_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "promotions": [
                {"reference_key": key.stable_key, "period_key": key.period_key, "run_id": run_id}
                for key, run_id in self.promotions
            ],
            "alerts": [item.to_dict() for item in self.alerts],
            "invalid": [item.to_dict() for item in self.invalid],
            "already_selected_run_ids": list(self.already_selected_run_ids),
        }


def _candidate(value: Any) -> ReportCandidate:
    candidate = getattr(value, "candidate", value)
    if not isinstance(candidate, ReportCandidate):
        raise TypeError("O backfill aceita ReportCandidate ou RegisteredReport.")
    deleted_at = getattr(value, "deleted_at", None)
    if deleted_at and not candidate.deleted_at:
        candidate = replace(candidate, deleted_at=str(deleted_at))
    return candidate


def plan_main_backfill(
    reports: Iterable[Any],
    *,
    used_history_run_ids: set[str] | frozenset[str],
    existing_main_run_ids: set[str] | frozenset[str] = frozenset(),
) -> MainBackfillPlan:
    groups: dict[str, tuple[ReportReferenceKey, list[ReportCandidate]]] = {}
    protected_reference_keys: set[str] = set()
    invalid: list[InvalidBackfillCandidate] = []
    already_selected: list[str] = []
    for value in reports:
        candidate = _candidate(value)
        eligibility = main_eligibility(candidate)
        if not eligibility.eligible:
            invalid.append(InvalidBackfillCandidate(candidate.run_id, eligibility.reasons))
            continue
        key = reference_key_for_candidate(candidate)
        if candidate.run_id in existing_main_run_ids:
            protected_reference_keys.add(key.stable_key)
            already_selected.append(candidate.run_id)
            continue
        group = groups.setdefault(key.stable_key, (key, []))
        group[1].append(candidate)

    for stable_key in protected_reference_keys:
        groups.pop(stable_key, None)

    promotions: list[tuple[ReportReferenceKey, str]] = []
    alerts: list[BackfillAlert] = []
    for stable_key in sorted(groups):
        key, candidates = groups[stable_key]
        candidates.sort(key=lambda item: item.run_id)
        historical = [
            item for item in candidates if item.run_id in used_history_run_ids
        ]
        selected = (
            candidates[0]
            if len(candidates) == 1
            else historical[0] if len(historical) == 1 else None
        )
        if selected is not None:
            promotions.append((key, selected.run_id))
            continue
        run_ids = tuple(item.run_id for item in candidates)
        alerts.append(BackfillAlert(
            code="MAIN_SELECTION_REQUIRED",
            reference_key=key.stable_key,
            run_ids=run_ids,
            message="Há mais de uma geração válida e nenhuma referência histórica inequívoca.",
        ))
    return MainBackfillPlan(
        promotions=tuple(promotions),
        alerts=tuple(alerts),
        invalid=tuple(sorted(invalid, key=lambda item: item.run_id)),
        already_selected_run_ids=tuple(sorted(already_selected)),
    )
