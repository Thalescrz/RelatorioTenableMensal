from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence

from tenable_reports.domain.normalization import NormalizedAsset, NormalizedFinding
from tenable_reports.domain.reporting import ReportingPeriod, iso_utc, parse_utc
from tenable_reports.domain.was import NormalizedWasFinding, build_was_report_data


METRIC_DEFINITION_VERSION = "report-definition-v1.2"
ACTIONABLE_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
SEVERITY_WEIGHT = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
OPEN_STATES = frozenset({"OPEN", "REOPENED"})


class Availability(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_COLLECTED = "NOT_COLLECTED"
    NO_DATA = "NO_DATA"


class AssetPopulationReason(StrEnum):
    OBSERVED_BY_SCAN = "OBSERVED_BY_SCAN"
    OBSERVED_BY_FINDING = "OBSERVED_BY_FINDING"
    EXCLUDED_INACTIVE_BEFORE_PERIOD = "EXCLUDED_INACTIVE_BEFORE_PERIOD"
    EXCLUDED_FIRST_SEEN_AFTER_PERIOD = "EXCLUDED_FIRST_SEEN_AFTER_PERIOD"
    EXCLUDED_STALE_BEFORE_PERIOD = "EXCLUDED_STALE_BEFORE_PERIOD"
    EXCLUDED_NO_PERIOD_EVIDENCE = "EXCLUDED_NO_PERIOD_EVIDENCE"
    EXCLUDED_MISSING_TIME_EVIDENCE = "EXCLUDED_MISSING_TIME_EVIDENCE"


class FindingPopulationReason(StrEnum):
    INCLUDED_OPEN = "INCLUDED_OPEN"
    INCLUDED_FIXED = "INCLUDED_FIXED"
    EXCLUDED_INFO = "EXCLUDED_INFO"
    EXCLUDED_UNSUPPORTED_SEVERITY = "EXCLUDED_UNSUPPORTED_SEVERITY"
    EXCLUDED_UNSUPPORTED_STATE = "EXCLUDED_UNSUPPORTED_STATE"
    EXCLUDED_EVENT_MISSING = "EXCLUDED_EVENT_MISSING"
    EXCLUDED_BEFORE_PERIOD = "EXCLUDED_BEFORE_PERIOD"
    EXCLUDED_AFTER_PERIOD = "EXCLUDED_AFTER_PERIOD"
    EXCLUDED_ORPHAN_ASSET = "EXCLUDED_ORPHAN_ASSET"
    EXCLUDED_ASSET_NOT_OBSERVED = "EXCLUDED_ASSET_NOT_OBSERVED"


@dataclass(frozen=True, slots=True)
class ReportQualityIssue:
    code: str
    severity: str
    message: str
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReportDataset:
    client_id: str
    run_id: str
    execution_type: str
    period: ReportingPeriod
    generated_at: datetime
    collection_timing: dict[str, Any]
    source_coverage: dict[str, Any]
    populations: dict[str, Any]
    metrics: dict[str, Any]
    top_assets: tuple[dict[str, Any], ...]
    top_open_vulnerabilities: tuple[dict[str, Any], ...]
    top_fixed_vulnerabilities: tuple[dict[str, Any], ...]
    top_resurfaced_vulnerabilities: tuple[dict[str, Any], ...]
    quality_issues: tuple[ReportQualityIssue, ...]
    was: dict[str, Any] | None = None
    top_web_vulnerabilities: tuple[dict[str, Any], ...] = ()
    customizations: dict[str, Any] | None = None
    table_provenance: dict[str, Any] | None = None
    collection_provenance: dict[str, Any] | None = None
    schema_version: int = 1
    metric_definition_version: str = METRIC_DEFINITION_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "metric_definition_version": self.metric_definition_version,
            "client_id": self.client_id,
            "run_id": self.run_id,
            "execution_type": self.execution_type,
            "period": self.period.to_dict(),
            "generated_at": iso_utc(self.generated_at),
            "collection_timing": self.collection_timing,
            "source_coverage": self.source_coverage,
            "populations": self.populations,
            "metrics": self.metrics,
            "top_assets": list(self.top_assets),
            "top_open_vulnerabilities": list(self.top_open_vulnerabilities),
            "top_fixed_vulnerabilities": list(self.top_fixed_vulnerabilities),
            "top_resurfaced_vulnerabilities": list(self.top_resurfaced_vulnerabilities),
            "was": self.was or {
                "availability": "NOT_COLLECTED",
                "applications": [],
                "top_vulnerabilities": [],
                "owasp": {},
            },
            "top_web_vulnerabilities": list(self.top_web_vulnerabilities),
            "quality_issues": [item.to_dict() for item in self.quality_issues],
            **({"table_provenance": self.table_provenance} if self.table_provenance else {}),
            **({"collection_provenance": self.collection_provenance} if self.collection_provenance else {}),
            **({"customizations": self.customizations} if self.customizations else {}),
        }


@dataclass(frozen=True, slots=True)
class ReportDatasetResult:
    dataset: ReportDataset
    observed_assets: tuple[NormalizedAsset, ...]
    included_findings: tuple[NormalizedFinding, ...]
    asset_population_reason: Mapping[str, AssetPopulationReason]
    finding_population_reason: Mapping[str, FindingPopulationReason]


def _finding_event(finding: NormalizedFinding) -> datetime | None:
    if finding.state in OPEN_STATES:
        return parse_utc(finding.last_found_at)
    if finding.state == "FIXED":
        return parse_utc(finding.last_fixed_at)
    return None


def _finding_reason(
    finding: NormalizedFinding,
    period: ReportingPeriod,
    *,
    include_info_severity: bool,
) -> FindingPopulationReason:
    if finding.state not in OPEN_STATES | {"FIXED"}:
        return FindingPopulationReason.EXCLUDED_UNSUPPORTED_STATE
    event = _finding_event(finding)
    if event is None:
        return FindingPopulationReason.EXCLUDED_EVENT_MISSING
    if event < period.start_at:
        return FindingPopulationReason.EXCLUDED_BEFORE_PERIOD
    if event >= period.end_at:
        return FindingPopulationReason.EXCLUDED_AFTER_PERIOD
    if finding.severity == "INFO" and not include_info_severity:
        return FindingPopulationReason.EXCLUDED_INFO
    allowed = set(ACTIONABLE_SEVERITIES)
    if include_info_severity:
        allowed.add("INFO")
    if finding.severity not in allowed:
        return FindingPopulationReason.EXCLUDED_UNSUPPORTED_SEVERITY
    return (
        FindingPopulationReason.INCLUDED_FIXED
        if finding.state == "FIXED"
        else FindingPopulationReason.INCLUDED_OPEN
    )


def _asset_reason(
    asset: NormalizedAsset,
    period: ReportingPeriod,
    finding_evidence: set[str],
) -> AssetPopulationReason:
    first_scan = parse_utc(asset.first_scan_at)
    last_scan = parse_utc(asset.last_scan_at)
    inactive_at_values = tuple(
        value for value in (parse_utc(asset.deleted_at), parse_utc(asset.terminated_at))
        if value is not None
    )
    inactive_at = min(inactive_at_values) if inactive_at_values else None
    if inactive_at is not None and inactive_at < period.start_at:
        return AssetPopulationReason.EXCLUDED_INACTIVE_BEFORE_PERIOD
    if first_scan is not None and first_scan >= period.end_at:
        return AssetPopulationReason.EXCLUDED_FIRST_SEEN_AFTER_PERIOD
    if period.contains(first_scan) or period.contains(last_scan):
        return AssetPopulationReason.OBSERVED_BY_SCAN
    if asset.asset_key in finding_evidence:
        return AssetPopulationReason.OBSERVED_BY_FINDING
    if last_scan is not None and last_scan < period.start_at:
        return AssetPopulationReason.EXCLUDED_STALE_BEFORE_PERIOD
    if first_scan is None and last_scan is None:
        return AssetPopulationReason.EXCLUDED_MISSING_TIME_EVIDENCE
    return AssetPopulationReason.EXCLUDED_NO_PERIOD_EVIDENCE


def _severity_counts(findings: Iterable[NormalizedFinding]) -> dict[str, int]:
    counts = Counter(item.severity for item in findings)
    return {severity.lower(): counts.get(severity, 0) for severity in ACTIONABLE_SEVERITIES}


def _availability(collected: bool, count: int) -> Availability:
    if not collected:
        return Availability.NOT_COLLECTED
    return Availability.AVAILABLE if count else Availability.NO_DATA


def _age_days(finding: NormalizedFinding, period: ReportingPeriod) -> int | None:
    first_found = parse_utc(finding.first_found_at)
    if first_found is None or first_found >= period.end_at:
        return None
    return max(0, int((period.end_at - first_found).total_seconds() // 86400))


def _aging_summary(
    findings: Sequence[NormalizedFinding], period: ReportingPeriod
) -> dict[str, int]:
    buckets = {
        "0_30_days": 0,
        "31_60_days": 0,
        "61_90_days": 0,
        "91_180_days": 0,
        "181_365_days": 0,
        "over_365_days": 0,
        "unknown": 0,
    }
    for finding in findings:
        age = _age_days(finding, period)
        if age is None:
            buckets["unknown"] += 1
        elif age <= 30:
            buckets["0_30_days"] += 1
        elif age <= 60:
            buckets["31_60_days"] += 1
        elif age <= 90:
            buckets["61_90_days"] += 1
        elif age <= 180:
            buckets["91_180_days"] += 1
        elif age <= 365:
            buckets["181_365_days"] += 1
        else:
            buckets["over_365_days"] += 1
    return buckets


def _severity_counts_where(
    findings: Iterable[NormalizedFinding], predicate: Any
) -> dict[str, int]:
    rows = tuple(item for item in findings if predicate(item))
    return _severity_counts(rows)


def _patch_summary(
    findings: Sequence[NormalizedFinding], period: ReportingPeriod
) -> tuple[int, dict[str, int]]:
    selected = tuple(
        item for item in findings
        if item.has_patch is True
        and (age := _age_days(item, period)) is not None
        and age > 30
    )
    return len(selected), _severity_counts(selected)


def _aging_by_severity(
    findings: Sequence[NormalizedFinding], period: ReportingPeriod
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for severity in ACTIONABLE_SEVERITIES:
        buckets = {
            "0_7_days": 0,
            "8_14_days": 0,
            "15_30_days": 0,
            "31_60_days": 0,
            "61_91_days": 0,
            "90_plus_days": 0,
            "unknown": 0,
        }
        for finding in findings:
            if finding.severity != severity:
                continue
            age = _age_days(finding, period)
            if age is None:
                buckets["unknown"] += 1
            elif age <= 7:
                buckets["0_7_days"] += 1
            elif age <= 14:
                buckets["8_14_days"] += 1
            elif age <= 30:
                buckets["15_30_days"] += 1
            elif age <= 60:
                buckets["31_60_days"] += 1
            elif age <= 90:
                buckets["61_91_days"] += 1
            else:
                buckets["90_plus_days"] += 1
        result[severity.lower()] = buckets
    return result


def _cvss_label(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score == 10.0:
        return "critical_10"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low"


def _vpr_label(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0:
        return "low"
    return "unknown"


def _cvss_metrics(
    open_findings: Sequence[NormalizedFinding],
    fixed_findings: Sequence[NormalizedFinding],
    period: ReportingPeriod,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    cvss_rows = []
    labels = (
        ("CVSSv3 10.0", "critical_10"),
        ("CVSSv3 7.0 - 9.9", "high"),
        ("CVSSv3 4.0 - 6.9", "medium"),
        ("CVSSv3 0.0 - 3.9", "low"),
    )
    for label, bucket in labels:
        opened = tuple(item for item in open_findings if _cvss_label(item.cvss3_base_score) == bucket)
        fixed = tuple(item for item in fixed_findings if _cvss_label(item.cvss3_base_score) == bucket)
        patched, _ = _patch_summary(opened, period)
        cvss_rows.append({
            "label": label,
            "mitigated": len(fixed),
            "non_mitigated": len(opened),
            "exploitable": sum(item.exploitable is True for item in opened),
            "patch_available_over_30_days": patched,
        })
    matrix_rows = []
    matrix_labels = (
        ("CVSSv3 Baixo (0.0-3.9)", "low"),
        ("CVSSv3 Médio (4.0-6.9)", "medium"),
        ("CVSSv3 Alto (VPR7.0-8.9)", "high"),
        ("CVSSv3 Crítico (VPR 9.0-10)", "critical_10"),
    )
    for label, cvss_bucket in matrix_labels:
        row: dict[str, Any] = {"label": label}
        for vpr_bucket in ("low", "medium", "high", "critical"):
            row[vpr_bucket] = sum(
                _cvss_label(item.cvss3_base_score) == cvss_bucket
                and _vpr_label(item.vpr_score) == vpr_bucket
                for item in open_findings
            )
        matrix_rows.append(row)
    vpr = {
        bucket: sum(_vpr_label(item.vpr_score) == bucket for item in open_findings)
        for bucket in ("critical", "high", "medium", "low")
    }
    return cvss_rows, matrix_rows, vpr


_ITP_OS_GROUP_ORDER = (
    "Windows",
    "Mac OS X",
    "Linux/Unix",
    "WEB",
    "Devices/Services",
)
_ITP_WINDOWS_PLUGIN_FAMILIES = frozenset({
    "windows",
    "windows : user management",
    "windows : microsoft bulletins",
})
_ITP_LINUX_PLUGIN_FAMILIES = frozenset({
    "red hat local security checks",
    "debian local security checks",
    "fedora local security checks",
    "gentoo local security checks",
    "suse local security checks",
    "freebsd local security checks",
    "ubuntu local security checks",
    "centos local security checks",
    "scientific linux local security checks",
    "oracle linux local security checks",
    "amazon linux local security checks",
    "hp-ux local security checks",
    "solaris local security checks",
    "aix local security checks",
    "slackware local security checks",
    "netware",
    "mandriva local security checks",
    "mandrake local security checks",
})
_ITP_MAC_PLUGIN_FAMILIES = frozenset({"macos x local security checks"})
_ITP_WEB_PLUGIN_FAMILIES = frozenset({
    "cgi abuses",
    "web servers",
    "cgi abuses : xss",
})


def _itp_operating_system_groups(finding: NormalizedFinding) -> tuple[str, ...]:
    family = (finding.plugin_family or "").strip().casefold()
    groups: list[str] = []
    if family in _ITP_WINDOWS_PLUGIN_FAMILIES:
        groups.append("Windows")
    elif family in _ITP_LINUX_PLUGIN_FAMILIES:
        groups.append("Linux/Unix")
    elif family in _ITP_MAC_PLUGIN_FAMILIES:
        groups.append("Mac OS X")
    elif family in _ITP_WEB_PLUGIN_FAMILIES:
        groups.append("WEB")
    if "service" in (finding.plugin_name or "").casefold():
        groups.append("Devices/Services")
    return tuple(groups)


def _operating_system_matrix(
    assets_by_key: Mapping[str, NormalizedAsset],
    open_findings: Sequence[NormalizedFinding],
    fixed_findings: Sequence[NormalizedFinding],
    period: ReportingPeriod,
    *,
    fixed_collected: bool,
) -> tuple[dict[str, Any], ...]:
    del assets_by_key
    rows = {
        group: {
            "operating_system": group,
            "non_mitigated": 0,
            "mitigated": 0 if fixed_collected else None,
            "exploitable": 0,
            "patch_available_over_30_days": 0,
        }
        for group in _ITP_OS_GROUP_ORDER
    }

    for finding in open_findings:
        for group in _itp_operating_system_groups(finding):
            row = rows[group]
            row["non_mitigated"] += 1
            row["exploitable"] += int(finding.exploitable is True)
            age = _age_days(finding, period)
            if finding.has_patch is True and age is not None and age > 30:
                row["patch_available_over_30_days"] += 1
    if fixed_collected:
        for finding in fixed_findings:
            for group in _itp_operating_system_groups(finding):
                rows[group]["mitigated"] += 1
    return tuple(rows[group] for group in _ITP_OS_GROUP_ORDER)


def _exploit_framework_matrix(
    open_findings: Sequence[NormalizedFinding],
) -> tuple[dict[str, Any], ...]:
    rows: dict[str, dict[str, Any]] = {}
    for finding in open_findings:
        for framework in finding.exploit_frameworks:
            row = rows.setdefault(framework, {
                "framework": framework,
                "total": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
            })
            row["total"] += 1
            severity = finding.severity.lower()
            if severity in {"critical", "high", "medium"}:
                row[severity] += 1
    return tuple(sorted(rows.values(), key=lambda row: (-row["total"], row["framework"].casefold())))


def _states_from_query(query: Mapping[str, Any] | None) -> set[str] | None:
    if not query:
        return None
    filters = query.get("filters")
    if not isinstance(filters, Mapping):
        return None
    states = filters.get("state")
    if not states:
        return None
    values = states if isinstance(states, list) else [states]
    return {str(value).upper() for value in values}


def _top_assets(
    assets_by_key: Mapping[str, NormalizedAsset],
    findings: Sequence[NormalizedFinding],
    limit: int,
) -> tuple[dict[str, Any], ...]:
    grouped: dict[str, list[NormalizedFinding]] = defaultdict(list)
    for finding in findings:
        if finding.state in OPEN_STATES and finding.asset_key:
            grouped[finding.asset_key].append(finding)
    rows: list[dict[str, Any]] = []
    for asset_key, asset_findings in grouped.items():
        asset = assets_by_key[asset_key]
        counts = _severity_counts(asset_findings)
        counts["info"] = sum(item.severity == "INFO" for item in asset_findings)
        total = len(asset_findings)
        exploitable = sum(item.exploitable is True for item in asset_findings)
        unknown = sum(item.exploitable is None for item in asset_findings)
        exploitable_value = None if unknown == total and total else exploitable
        if exploitable_value is not None and not 0 <= exploitable_value <= total:
            raise ValueError("Exploitable precisa ser subconjunto do Total por ativo.")
        rows.append({
            "asset_key": asset.asset_key,
            "source_asset_id": asset.source_asset_id,
            "asset_name": asset.display_name or (asset.hostnames[0] if asset.hostnames else None),
            "ip_address": asset.ipv4s[0] if asset.ipv4s else None,
            **counts,
            "total": total,
            "exploitable": exploitable_value,
            "exploitable_availability": (
                "NOT_COLLECTED" if exploitable_value is None else
                "PARTIAL" if unknown else "AVAILABLE"
            ),
            "exploitability_unknown": unknown,
        })
    rows.sort(key=lambda row: (
        -row["total"], -row["critical"], -row["high"], -(row["exploitable"] or 0),
        row["asset_key"],
    ))
    return tuple(rows[:limit])


def _most_complete(values: Iterable[str | None]) -> str | None:
    candidates = {value.strip() for value in values if value and value.strip()}
    return sorted(candidates, key=lambda value: (-len(value), value.casefold()))[0] if candidates else None


def _top_plugins(
    assets_by_key: Mapping[str, NormalizedAsset],
    findings: Sequence[NormalizedFinding],
    limit: int,
    *,
    include_output: bool,
) -> tuple[dict[str, Any], ...]:
    grouped: dict[int, list[NormalizedFinding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.plugin_id].append(finding)
    rows: list[dict[str, Any]] = []
    for plugin_id, instances in grouped.items():
        severities = {item.severity for item in instances}
        max_severity = max(severities, key=lambda value: SEVERITY_WEIGHT.get(value, -1))
        vpr_values = [item.vpr_score for item in instances if item.vpr_score is not None]
        first_dates = [parse_utc(item.first_found_at) for item in instances]
        first_dates = [item for item in first_dates if item is not None]
        asset_keys = sorted({item.asset_key for item in instances if item.asset_key})
        host_instances: list[dict[str, Any]] = []
        for item in sorted(
            instances,
            key=lambda value: (value.source_asset_id, value.port, value.protocol, value.finding_key),
        ):
            asset = assets_by_key.get(item.asset_key or "")
            host = {
                "asset_key": item.asset_key,
                "source_asset_id": item.source_asset_id,
                "asset_name": (
                    asset.display_name or (asset.hostnames[0] if asset and asset.hostnames else None)
                    if asset else None
                ),
                "ip_addresses": list(asset.ipv4s) if asset else [],
                "port": item.port,
                "protocol": item.protocol,
                "service": item.service,
            }
            if include_output:
                host["plugin_output"] = item.plugin_output
            host_instances.append(host)
        cves = sorted({cve for item in instances for cve in item.cves})
        reference_urls = {
            reference
            for item in instances
            for reference in item.references
            if reference.startswith(("https://", "http://"))
        }
        reference_urls.add(f"https://www.tenable.com/plugins/nessus/{plugin_id}")
        reference_urls.update(
            f"https://www.cve.org/CVERecord?id={cve}"
            for cve in cves
            if cve.upper().startswith("CVE-")
        )
        exploitable_values = [item.exploitable for item in instances]
        rows.append({
            "plugin_id": plugin_id,
            "plugin_name": _most_complete(item.plugin_name for item in instances),
            "plugin_family": _most_complete(item.plugin_family for item in instances),
            "severity": max_severity,
            "vpr_score": max(vpr_values) if vpr_values else None,
            "exploitable": True if any(value is True for value in exploitable_values) else (
                False if any(value is False for value in exploitable_values) else None
            ),
            "affected_assets": len(asset_keys),
            "finding_instances": len(instances),
            "oldest_first_found_at": iso_utc(min(first_dates)) if first_dates else None,
            "cves": cves,
            "reference_urls": sorted(reference_urls),
            "synopsis": _most_complete(item.synopsis for item in instances),
            "description": _most_complete(item.description for item in instances),
            "solution": _most_complete(item.solution for item in instances),
            "hosts": host_instances,
        })
    rows.sort(key=lambda row: (
        -(row["vpr_score"] if row["vpr_score"] is not None else -1.0),
        -SEVERITY_WEIGHT.get(row["severity"], -1),
        -(1 if row["exploitable"] is True else 0),
        -row["affected_assets"],
        row["oldest_first_found_at"] or "9999",
        row["plugin_id"],
    ))
    return tuple(rows[:limit])


def build_report_dataset(
    *,
    client_id: str,
    run_id: str,
    execution_type: str = "UNSPECIFIED",
    period: ReportingPeriod,
    assets: Iterable[NormalizedAsset],
    findings: Iterable[NormalizedFinding],
    generated_at: datetime,
    collection_completed_at: datetime,
    finding_query: Mapping[str, Any] | None = None,
    include_info_severity: bool = False,
    include_output: bool = False,
    top_assets_limit: int = 10,
    top_vulnerabilities_limit: int = 5,
    late_collection_grace_days: int = 1,
    tag_scope: Mapping[str, Any] | None = None,
    was_findings: Iterable[NormalizedWasFinding] = (),
    was_collected: bool = False,
) -> ReportDatasetResult:
    asset_rows = tuple(assets)
    finding_rows = tuple(findings)
    assets_by_key = {item.asset_key: item for item in asset_rows}

    preliminary_reasons = {
        finding.finding_key: _finding_reason(
            finding, period, include_info_severity=include_info_severity
        )
        for finding in finding_rows
    }
    period_evidence = {
        finding.asset_key
        for finding in finding_rows
        if finding.asset_key
        and preliminary_reasons[finding.finding_key] in {
            FindingPopulationReason.INCLUDED_OPEN,
            FindingPopulationReason.INCLUDED_FIXED,
            FindingPopulationReason.EXCLUDED_INFO,
        }
    }
    asset_reasons = {
        asset.asset_key: _asset_reason(asset, period, period_evidence)
        for asset in asset_rows
    }
    observed_reason_values = {
        AssetPopulationReason.OBSERVED_BY_SCAN,
        AssetPopulationReason.OBSERVED_BY_FINDING,
    }
    observed_assets = tuple(
        asset for asset in asset_rows if asset_reasons[asset.asset_key] in observed_reason_values
    )
    observed_keys = {item.asset_key for item in observed_assets}

    final_reasons: dict[str, FindingPopulationReason] = {}
    included_findings: list[NormalizedFinding] = []
    for finding in finding_rows:
        reason = preliminary_reasons[finding.finding_key]
        if reason in {FindingPopulationReason.INCLUDED_OPEN, FindingPopulationReason.INCLUDED_FIXED}:
            if not finding.asset_key:
                reason = FindingPopulationReason.EXCLUDED_ORPHAN_ASSET
            elif finding.asset_key not in observed_keys:
                reason = FindingPopulationReason.EXCLUDED_ASSET_NOT_OBSERVED
            else:
                included_findings.append(finding)
        final_reasons[finding.finding_key] = reason

    open_findings = tuple(item for item in included_findings if item.state in OPEN_STATES)
    fixed_findings = tuple(item for item in included_findings if item.state == "FIXED")
    resurfaced_findings = tuple(
        item for item in open_findings
        if item.state == "REOPENED" and period.contains(parse_utc(item.resurfaced_at))
    )

    requested_states = _states_from_query(finding_query)
    open_collected = requested_states is None or bool(requested_states & OPEN_STATES)
    fixed_collected = requested_states is None or "FIXED" in requested_states
    exploitable_unknown = sum(item.exploitable is None for item in open_findings)
    open_exploitable = sum(item.exploitable is True for item in open_findings)
    vulnerable_assets = len({item.asset_key for item in open_findings if item.asset_key})
    new_rows = tuple(item for item in open_findings if period.contains(parse_utc(item.first_found_at)))
    new_open_findings = len(new_rows)
    patch_total, patch_by_severity = _patch_summary(open_findings, period)
    cvss_rows, cvss_vpr_matrix, vpr_rating = _cvss_metrics(
        open_findings, fixed_findings, period
    )

    asset_reason_counts = Counter(reason.value for reason in asset_reasons.values())
    finding_reason_counts = Counter(reason.value for reason in final_reasons.values())
    if sum(asset_reason_counts.values()) != len(asset_rows):
        raise ValueError("Reconciliacao da populacao de ativos inconsistente.")
    if sum(finding_reason_counts.values()) != len(finding_rows):
        raise ValueError("Reconciliacao da populacao de findings inconsistente.")

    lag_seconds = (collection_completed_at - period.end_at).total_seconds()
    grace_seconds = late_collection_grace_days * 86400
    timing_status = "ON_TIME" if 0 <= lag_seconds <= grace_seconds else (
        "BEFORE_PERIOD_END" if lag_seconds < 0 else "LATE"
    )
    quality: list[ReportQualityIssue] = []
    if timing_status == "LATE":
        quality.append(ReportQualityIssue(
            code="COLLECTION_AFTER_MONTH_CLOSE_GRACE",
            severity="WARNING",
            count=1,
            message=(
                "A coleta ocorreu apos a tolerancia do fechamento; estados correntes podem "
                "refletir alteracoes posteriores ao periodo."
            ),
        ))
    elif timing_status == "BEFORE_PERIOD_END":
        quality.append(ReportQualityIssue(
            code="COLLECTION_BEFORE_PERIOD_END",
            severity="ERROR",
            count=1,
            message="A coleta ocorreu antes do encerramento do periodo solicitado.",
        ))
    if not fixed_collected:
        quality.append(ReportQualityIssue(
            code="FIXED_STATE_NOT_COLLECTED",
            severity="WARNING",
            message="Findings FIXED nao fizeram parte do export; mitigadas ficam indisponiveis.",
        ))
    if exploitable_unknown:
        quality.append(ReportQualityIssue(
            code="EXPLOITABILITY_UNKNOWN",
            severity="WARNING",
            count=exploitable_unknown,
            message="Parte da populacao aberta nao possui sinal validado de exploitabilidade.",
        ))
    invalid_age_count = sum(_age_days(item, period) is None for item in open_findings)
    if invalid_age_count:
        quality.append(ReportQualityIssue(
            code="OPEN_FINDING_AGE_UNAVAILABLE",
            severity="WARNING",
            count=invalid_age_count,
            message="Parte da populacao aberta nao possui first_found valido para aging.",
        ))

    asset_lookup = {item.asset_key: item for item in observed_assets}
    top_assets = _top_assets(asset_lookup, open_findings, top_assets_limit)
    top_open = _top_plugins(
        asset_lookup, open_findings, top_vulnerabilities_limit, include_output=include_output
    )
    top_fixed = _top_plugins(
        asset_lookup, fixed_findings, top_vulnerabilities_limit, include_output=include_output
    ) if fixed_collected else ()
    top_resurfaced = _top_plugins(
        asset_lookup, resurfaced_findings, top_vulnerabilities_limit, include_output=include_output
    )
    operating_system_matrix = _operating_system_matrix(
        asset_lookup,
        open_findings,
        fixed_findings,
        period,
        fixed_collected=fixed_collected,
    )
    network_tag_snapshots: list[dict[str, Any]] = []
    selected_tag_rows = tag_scope.get("selected_tags") if tag_scope else None
    if isinstance(selected_tag_rows, list):
        for tag in selected_tag_rows:
            if not isinstance(tag, Mapping):
                continue
            source_ids = {
                str(value) for value in (tag.get("asset_ids") or []) if str(value).strip()
            }
            slice_assets = {
                key: asset for key, asset in asset_lookup.items()
                if asset.source_asset_id in source_ids
            }
            slice_findings = tuple(
                finding for finding in open_findings
                if finding.asset_key in slice_assets
            )
            network_tag_snapshots.append({
                "tag_uuid": tag.get("uuid"),
                "category": tag.get("category_name"),
                "network": tag.get("value"),
                "label": tag.get("value"),
                "period_id": period.period_id,
                "asset_population": len(slice_assets),
                "vulnerable_assets": len({
                    item.asset_key for item in slice_findings if item.asset_key
                }),
                "assets": list(_top_assets(slice_assets, slice_findings, 20)),
            })

    was, top_web, was_population = build_was_report_data(
        was_findings,
        period=period,
        collected=was_collected,
        include_info_severity=include_info_severity,
        top_limit=top_vulnerabilities_limit,
    )

    metrics = {
        "non_mitigated": {
            "availability": _availability(open_collected, len(open_findings)).value,
            "grain": "finding_instance",
            "total": len(open_findings) if open_collected else None,
            "by_severity": _severity_counts(open_findings) if open_collected else None,
            "exploitable": open_exploitable if open_collected else None,
            "exploitable_by_severity": _severity_counts_where(
                open_findings, lambda item: item.exploitable is True
            ) if open_collected else None,
            "exploitability_unknown": exploitable_unknown if open_collected else None,
            "vulnerable_assets": vulnerable_assets if open_collected else None,
            "new_in_period": new_open_findings if open_collected else None,
            "new_by_severity": _severity_counts(new_rows) if open_collected else None,
            "new_exploitable": sum(item.exploitable is True for item in new_rows)
            if open_collected else None,
            "patch_available_over_30_days": patch_total if open_collected else None,
            "patch_available_over_30_days_by_severity": patch_by_severity
            if open_collected else None,
            "aging": _aging_summary(open_findings, period) if open_collected else None,
            "aging_by_severity": _aging_by_severity(open_findings, period)
            if open_collected else None,
        },
        "mitigated": {
            "availability": _availability(fixed_collected, len(fixed_findings)).value,
            "grain": "finding_instance",
            "total": len(fixed_findings) if fixed_collected else None,
            "by_severity": _severity_counts(fixed_findings) if fixed_collected else None,
            "exploitable": sum(item.exploitable is True for item in fixed_findings)
            if fixed_collected else None,
        },
        "resurfaced": {
            "availability": _availability(open_collected, len(resurfaced_findings)).value,
            "grain": "finding_instance",
            "total": len(resurfaced_findings) if open_collected else None,
            "by_severity": _severity_counts(resurfaced_findings) if open_collected else None,
            "exploitable": sum(item.exploitable is True for item in resurfaced_findings)
            if open_collected else None,
            "definition": "state=REOPENED and resurfaced_at within period",
        },
        "assets": {
            "normalized_total": len(asset_rows),
            "observed_in_period": len(observed_assets),
            "excluded_from_period": len(asset_rows) - len(observed_assets),
            "vulnerable_in_period": vulnerable_assets,
        },
        "by_operating_system": {
            "availability": _availability(
                open_collected,
                sum(row["non_mitigated"] + (row["mitigated"] or 0)
                    for row in operating_system_matrix),
            ).value,
            "grain": "finding_instance_grouped_by_legacy_itp_plugin_family",
            "rows": list(operating_system_matrix) if open_collected else None,
        },
        "by_cvss": cvss_rows if open_collected else None,
        "cvss_vpr_matrix": cvss_vpr_matrix if open_collected else None,
        "vpr_rating": vpr_rating if open_collected else None,
        "by_exploit_framework": list(_exploit_framework_matrix(open_findings))
        if open_collected else None,
    }
    source_coverage = {
        "requested_finding_states": sorted(requested_states) if requested_states else "ALL_OR_UNSPECIFIED",
        "open_metrics_collected": open_collected,
        "fixed_metrics_collected": fixed_collected,
        "plugin_output_included": include_output,
        "general_collection_filtered_by_tags": False,
        "tag_comparison_snapshot_available": bool(network_tag_snapshots),
        "selected_tag_category": tag_scope.get("category_name") if tag_scope else None,
        "selected_tag_count": len(network_tag_snapshots),
        "was_findings_collected": was_collected,
        "was_plugin_output_included": bool(was_collected),
    }
    general_filter = {
        "source": "Tenable Vulnerability Management",
        "view": "Explore > Findings > Vulnerabilities",
        "period_start_at": iso_utc(period.start_at),
        "period_end_at": iso_utc(period.end_at),
        "timezone": period.timezone,
        "severities": [
            *ACTIONABLE_SEVERITIES,
            *(["INFO"] if include_info_severity else []),
        ],
        "scope": "GENERAL",
    }
    table_provenance = {
        "version": "table-provenance-v1",
        "tables": {
            "overview": {
                **general_filter,
                "validation_queries": [
                    {
                        "label": "Não mitigadas",
                        "states": ["OPEN", "REOPENED"],
                        "date_fields": ["Last Seen"],
                    },
                    {
                        "label": "Mitigadas",
                        "states": ["FIXED"],
                        "date_fields": ["Last Fixed"],
                    },
                ],
                "rule": (
                    "contagem por instância de finding; Explorável usa somente "
                    "Plugin > Exploit Available; Patch > 30d exige patch disponível "
                    "e First Seen anterior a 30 dias"
                ),
            },
            "top_assets": {
                **general_filter,
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Asset",
                "order_by": "Total de findings (decrescente)",
                "limit": 10,
                "rule": (
                    "contagem por instância de finding; Exploitable usa somente "
                    "Plugin > Exploit Available"
                ),
            },
            "top_open_vulnerabilities": {
                **general_filter,
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Plugin ID",
                "order_by": "VPR (decrescente)",
                "limit": top_vulnerabilities_limit,
                "rule": "ranking local por VPR, severidade e ativos afetados",
            },
            "top_fixed_vulnerabilities": {
                **general_filter,
                "states": ["FIXED"],
                "date_fields": ["Last Fixed"],
                "group_by": "Plugin ID",
                "order_by": "VPR (decrescente)",
                "limit": top_vulnerabilities_limit,
                "rule": "ranking local por VPR, severidade e ativos afetados",
            },
            "top_resurfaced_vulnerabilities": {
                **general_filter,
                "states": ["REOPENED"],
                "date_fields": ["Last Seen", "Resurfaced Date"],
                "group_by": "Plugin ID",
                "order_by": "VPR (decrescente)",
                "limit": top_vulnerabilities_limit,
                "rule": "somente findings ressurgidos dentro do período",
            },
            "was_applications": {
                **general_filter,
                "source": "Tenable Web App Scanning",
                "view": "WAS > Findings",
                "scope": "WAS_GENERAL",
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Application",
                "order_by": "Total de findings (decrescente)",
                "rule": "contagem por instância de finding WEB",
            },
            "was_top_vulnerabilities": {
                **general_filter,
                "source": "Tenable Web App Scanning",
                "view": "WAS > Findings",
                "scope": "WAS_GENERAL",
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Plugin ID",
                "order_by": "VPR (decrescente)",
                "limit": top_vulnerabilities_limit,
                "rule": "ranking local por VPR, severidade e aplicações afetadas",
            },
            "was_owasp": {
                **general_filter,
                "source": "Tenable Web App Scanning",
                "view": "WAS > Findings",
                "scope": "WAS_GENERAL",
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "OWASP 2021 e Plugin ID",
                "rule": "cada tabela aplica a categoria OWASP indicada na própria nota",
            },
            "top_web_vulnerabilities": {
                **general_filter,
                "source": "Tenable Web App Scanning",
                "view": "WAS > Findings",
                "scope": "WAS_GENERAL",
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Plugin ID",
                "order_by": "VPR (decrescente)",
                "limit": top_vulnerabilities_limit,
                "rule": "URIs afetadas pelo Plugin ID indicado na própria nota",
            },
            "by_operating_system": {
                **general_filter,
                "validation_queries": [
                    {
                        "label": "Não mitigadas, exploráveis e patch > 30d",
                        "states": ["OPEN", "REOPENED"],
                        "date_fields": ["Last Seen"],
                    },
                    {
                        "label": "Mitigadas",
                        "states": ["FIXED"],
                        "date_fields": ["Last Fixed"],
                    },
                ],
                "group_by": "família do plugin",
                "rule": (
                    "Windows, Mac OS X, Linux/Unix e WEB por Plugin Family; "
                    "Devices/Services por Plugin Name contendo service; categorias independentes"
                ),
            },
            "by_cvss": {
                **general_filter,
                "validation_queries": [
                    {
                        "label": "Não mitigadas, exploráveis e patch > 30d",
                        "states": ["OPEN", "REOPENED"],
                        "date_fields": ["Last Seen"],
                    },
                    {
                        "label": "Mitigadas",
                        "states": ["FIXED"],
                        "date_fields": ["Last Fixed"],
                    },
                ],
                "group_by": "CVSSv3 Base Score",
                "rule": "faixas locais: 10; 7,0–9,9; 4,0–6,9; 0,0–3,9",
            },
            "cvss_vpr_matrix": {
                **general_filter,
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "CVSSv3 Base Score e VPR",
                "rule": "cruzamento local das faixas de CVSS v3 e VPR",
            },
            "vpr_rating": {
                **general_filter,
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "VPR",
                "rule": "faixas locais: 9,0–10; 7,0–8,9; 4,0–6,9; 0,1–3,9",
            },
            "state_summary": {
                **general_filter,
                "validation_queries": [
                    {
                        "label": "Novo",
                        "states": ["OPEN", "REOPENED"],
                        "date_fields": ["Last Seen", "First Seen"],
                    },
                    {
                        "label": "Ativo/não mitigado",
                        "states": ["OPEN", "REOPENED"],
                        "date_fields": ["Last Seen"],
                    },
                    {
                        "label": "Corrigido",
                        "states": ["FIXED"],
                        "date_fields": ["Last Fixed"],
                    },
                    {
                        "label": "Ressurgido",
                        "states": ["REOPENED"],
                        "date_fields": ["Last Seen", "Resurfaced Date"],
                    },
                ],
                "rule": "Explorável usa somente Plugin > Exploit Available",
            },
            "aging_by_severity": {
                **general_filter,
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Severity",
                "rule": "idade calculada entre First Seen e o fim do período",
            },
            "by_exploit_framework": {
                **general_filter,
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Exploit Framework",
                "rule": (
                    "flags Canvas, Core Impact, D2 Elliot, ExploitHub e Metasploit; "
                    "pode haver múltipla contagem"
                ),
            },
            "previous_period_overview": {
                **general_filter,
                "validation_queries": [
                    {
                        "label": "Não mitigadas",
                        "states": ["OPEN", "REOPENED"],
                        "date_fields": ["Last Seen"],
                    },
                    {
                        "label": "Mitigadas",
                        "states": ["FIXED"],
                        "date_fields": ["Last Fixed"],
                    },
                ],
                "rule": "valores recuperados do relatório marcado como main do período anterior",
            },
            "plugin_family": {
                **general_filter,
                "states": ["FIXED"],
                "date_fields": ["Last Fixed"],
                "group_by": "Plugin Family",
                "order_by": "Total de findings (decrescente)",
                "rule": "contagem por instância de finding",
            },
            "eol_assets": {
                **general_filter,
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Asset",
                "rule": "catálogo textual local de plugins relacionados a fim de suporte",
            },
            "eol_software": {
                **general_filter,
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Plugin ID",
                "rule": "catálogo textual local de plugins relacionados a fim de suporte",
            },
            "container_images": {
                **general_filter,
                "source": "Tenable Cloud Security",
                "view": "Cloud Security > Findings > Container Images",
                "states": [],
                "platform_validation_available": False,
                "group_by": "Repository e Tag",
                "order_by": "Total de findings (decrescente)",
                "limit": 5,
                "rule": "contagem de vulnerabilidades por imagem de container",
            },
            "container_findings": {
                **general_filter,
                "source": "Tenable Cloud Security",
                "view": "Cloud Security > Findings > Container Images",
                "states": [],
                "platform_validation_available": False,
                "group_by": "CVE",
                "rule": "findings pertencentes à imagem indicada acima da tabela",
            },
            "attack_vectors": {
                **general_filter,
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "platform_filters": {"Plugin > Exploit Available": "Yes"},
                "group_by": "CVSSv3 Attack Vector",
                "rule": (
                    "somente Plugin > Exploit Available = Yes; vetores Network, "
                    "Adjacent e Local"
                ),
            },
            "was_unsupported_tech": {
                **general_filter,
                "source": "Tenable Web App Scanning",
                "view": "WAS > Findings",
                "scope": "WAS_GENERAL",
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
                "group_by": "Plugin ID",
                "rule": "catálogo textual local de tecnologias WEB sem suporte",
            },
            "network_tag_snapshots": [
                {
                    **general_filter,
                    "scope": "NETWORK_COMPARISON_ONLY",
                    "states": ["OPEN", "REOPENED"],
                    "date_fields": ["Last Seen"],
                    "group_by": "Asset",
                    "order_by": "Total de findings (decrescente)",
                    "limit": 20,
                    "rule": "mesma tag/rede comparada entre períodos; não filtra o relatório geral",
                    "tag_uuid": item.get("tag_uuid"),
                    "tag_category": item.get("category"),
                    "tag_value": item.get("network") or item.get("label"),
                }
                for item in network_tag_snapshots
            ],
            "network_asset_movement": [
                {
                    **general_filter,
                    "scope": "NETWORK_COMPARISON_ONLY",
                    "validation_queries": [
                        {
                            "label": "Consulta 1",
                            "states": ["OPEN", "REOPENED"],
                            "date_fields": ["Last Seen"],
                        },
                        {
                            "label": "Consulta 2",
                            "states": ["OPEN", "REOPENED"],
                            "date_fields": ["Last Seen"],
                        },
                    ],
                    "rule": "variação da posição e do total do mesmo ativo entre os dois períodos",
                    "tag_uuid": item.get("tag_uuid"),
                    "tag_category": item.get("category"),
                    "tag_value": item.get("network") or item.get("label"),
                }
                for item in network_tag_snapshots
            ],
        },
    }
    collection_timing = {
        "collection_completed_at": iso_utc(collection_completed_at),
        "period_end_at": iso_utc(period.end_at),
        "lag_seconds": int(lag_seconds),
        "lag_days": round(lag_seconds / 86400, 3),
        "grace_days": late_collection_grace_days,
        "status": timing_status,
        "recommended_schedule": (
            "first day of month in report timezone"
            if execution_type == "AUTOMATIC_MONTHLY" else None
        ),
    }
    populations = {
        "assets": {
            "input": len(asset_rows),
            "observed": len(observed_assets),
            "excluded": len(asset_rows) - len(observed_assets),
            "by_reason": dict(sorted(asset_reason_counts.items())),
        },
        "findings": {
            "input": len(finding_rows),
            "included": len(included_findings),
            "excluded": len(finding_rows) - len(included_findings),
            "by_reason": dict(sorted(finding_reason_counts.items())),
        },
        "was_findings": was_population,
    }
    return ReportDatasetResult(
        dataset=ReportDataset(
            client_id=client_id,
            run_id=run_id,
            execution_type=execution_type,
            period=period,
            generated_at=generated_at,
            collection_timing=collection_timing,
            source_coverage=source_coverage,
            populations=populations,
            metrics=metrics,
            top_assets=top_assets,
            top_open_vulnerabilities=top_open,
            top_fixed_vulnerabilities=top_fixed,
            top_resurfaced_vulnerabilities=top_resurfaced,
            quality_issues=tuple(quality),
            was=was,
            top_web_vulnerabilities=top_web,
            customizations=(
                {"network_tag_snapshots": network_tag_snapshots}
                if network_tag_snapshots else None
            ),
            table_provenance=table_provenance,
        ),
        observed_assets=observed_assets,
        included_findings=tuple(included_findings),
        asset_population_reason=asset_reasons,
        finding_population_reason=final_reasons,
    )
