"""Conservative reconstruction for open findings observed after month close."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable

from tenable_reports.domain.normalization import NormalizedFinding
from tenable_reports.domain.reporting import ReportingPeriod, iso_utc, parse_utc


OPEN_STATES = frozenset({"OPEN", "REOPENED"})
ACTIONABLE_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


@dataclass(frozen=True, slots=True)
class LateCollectionReconciliation:
    findings: tuple[NormalizedFinding, ...]
    adjusted_finding_keys: frozenset[str]
    by_severity: dict[str, int]

    @property
    def applied(self) -> bool:
        return bool(self.adjusted_finding_keys)

    def audit(self) -> dict[str, object]:
        severities = {
            severity.lower(): int(self.by_severity.get(severity, 0))
            for severity in ACTIONABLE_SEVERITIES
        }
        informational = int(self.by_severity.get("INFO", 0)) + int(
            self.by_severity.get("INFORMATIONAL", 0)
        )
        if informational:
            severities["informational"] = informational
        return {
            "status": "APPLIED",
            "method": "confirmed_open_temporal_continuity",
            "adjusted_open_findings": len(self.adjusted_finding_keys),
            "by_severity": severities,
        }


def _eligible(
    finding: NormalizedFinding,
    *,
    period: ReportingPeriod,
    include_info_severity: bool,
) -> bool:
    state = str(finding.state or "").upper()
    severity = str(finding.severity or "").upper()
    if state not in OPEN_STATES or finding.asset_key is None:
        return False
    if severity in {"INFO", "INFORMATIONAL"} and not include_info_severity:
        return False
    if severity not in ACTIONABLE_SEVERITIES and severity not in {
        "INFO",
        "INFORMATIONAL",
    }:
        return False
    first_found = parse_utc(finding.first_found_at)
    last_found = parse_utc(finding.last_found_at)
    if first_found is None or first_found >= period.end_at:
        return False
    if last_found is None or last_found < period.end_at:
        return False
    if state == "REOPENED":
        resurfaced = parse_utc(finding.resurfaced_at)
        if resurfaced is None or resurfaced >= period.end_at:
            return False
    return True


def reconcile_late_open_findings(
    findings: Iterable[NormalizedFinding],
    *,
    period: ReportingPeriod,
    collection_completed_at: datetime,
    grace_days: int,
    include_info_severity: bool,
) -> LateCollectionReconciliation:
    """Return an effective period view while preserving the source objects."""

    rows = tuple(findings)
    boundary = period.end_at + timedelta(days=max(0, int(grace_days)))
    if collection_completed_at <= boundary:
        return LateCollectionReconciliation(
            findings=rows,
            adjusted_finding_keys=frozenset(),
            by_severity={},
        )

    effective_last_found = iso_utc(period.end_at - timedelta(microseconds=1))
    adjusted: list[NormalizedFinding] = []
    keys: set[str] = set()
    severities: Counter[str] = Counter()
    for finding in rows:
        if not _eligible(
            finding,
            period=period,
            include_info_severity=include_info_severity,
        ):
            adjusted.append(finding)
            continue
        adjusted.append(replace(finding, last_found_at=effective_last_found))
        keys.add(finding.finding_key)
        severities[str(finding.severity or "").upper()] += 1
    return LateCollectionReconciliation(
        findings=tuple(adjusted),
        adjusted_finding_keys=frozenset(keys),
        by_severity=dict(severities),
    )


__all__ = [
    "LateCollectionReconciliation",
    "reconcile_late_open_findings",
]

