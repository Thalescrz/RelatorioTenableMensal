from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

from tenable_reports.domain.normalization import NormalizedAsset, NormalizedFinding
from tenable_reports.domain.reporting import ReportingPeriod, parse_utc
from tenable_reports.domain.was import NormalizedWasFinding


OPEN_STATES = frozenset({"OPEN", "REOPENED"})
SEVERITY_WEIGHT = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}


class IntelligenceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NO_OCCURRENCES = "NO_OCCURRENCES"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class UnsupportedCatalog:
    version: str
    patterns: tuple[str, ...]
    exclusions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntelligenceResult:
    data: dict[str, Any]
    statuses: dict[str, str]
    provenance: dict[str, Any]


def load_unsupported_catalog(path: str | Path | None = None) -> UnsupportedCatalog:
    source = Path(path) if path else Path(__file__).parents[1] / "catalogs" / "unsupported_signals_v1.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    return UnsupportedCatalog(
        version=str(raw["version"]),
        patterns=tuple(str(item) for item in raw.get("patterns") or ()),
        exclusions=tuple(str(item) for item in raw.get("exclusions") or ()),
    )


def _explicit_unsupported(record: Any, catalog: UnsupportedCatalog) -> tuple[str, str] | None:
    for field in ("plugin_name", "synopsis", "description"):
        value = str(getattr(record, field, None) or "")
        lowered = value.casefold()
        if any(exclusion.casefold() in lowered for exclusion in catalog.exclusions):
            continue
        for pattern in catalog.patterns:
            if re.search(pattern, value, flags=re.IGNORECASE):
                return field, pattern
    return None


def build_scan_auth_health(
    assets: Iterable[NormalizedAsset], period: ReportingPeriod
) -> dict[str, int]:
    # A população recebida já é a população observada e reconciliada do dataset.
    observed = tuple(assets)
    success = sum(
        period.contains(parse_utc(asset.last_authenticated_scan_at)) for asset in observed
    )
    return {"success": success, "failure": len(observed) - success, "total": len(observed)}


def build_plugin_family(
    findings: Iterable[NormalizedFinding], period: ReportingPeriod
) -> list[dict[str, Any]]:
    counts = Counter(
        finding.plugin_family or "Não informado"
        for finding in findings
        if finding.state == "FIXED" and period.contains(parse_utc(finding.last_fixed_at))
    )
    return [
        {"family": family, "total": total}
        for family, total in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    ]


def build_eol_data(
    findings: Iterable[NormalizedFinding],
    assets: Iterable[NormalizedAsset],
    catalog: UnsupportedCatalog,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    selected: list[tuple[NormalizedFinding, tuple[str, str]]] = []
    for finding in findings:
        if finding.state not in OPEN_STATES:
            continue
        match = _explicit_unsupported(finding, catalog)
        if match:
            selected.append((finding, match))

    asset_lookup = {asset.asset_key: asset for asset in assets}
    by_asset: dict[str, list[NormalizedFinding]] = defaultdict(list)
    by_plugin: dict[tuple[int, str, str, str], list[NormalizedFinding]] = defaultdict(list)
    evidence: list[dict[str, str]] = []
    for finding, (field, pattern) in selected:
        if finding.asset_key:
            by_asset[finding.asset_key].append(finding)
        by_plugin[(
            finding.plugin_id,
            finding.plugin_name or "",
            finding.plugin_family or "",
            finding.severity,
        )].append(finding)
        evidence.append({"finding_key": finding.finding_key, "field": field, "pattern": pattern})

    asset_rows: list[dict[str, Any]] = []
    for asset_key, rows in by_asset.items():
        asset = asset_lookup.get(asset_key)
        counts = Counter(item.severity for item in rows)
        asset_rows.append({
            "asset_key": asset_key,
            "ip_address": asset.ipv4s[0] if asset and asset.ipv4s else "",
            "asset_name": asset.display_name if asset else "",
            "critical": counts["CRITICAL"], "high": counts["HIGH"],
            "medium": counts["MEDIUM"], "low": counts["LOW"], "total": len(rows),
        })
    asset_rows.sort(key=lambda item: (-item["total"], str(item["asset_name"]).casefold()))

    software_rows = [
        {"plugin_id": key[0], "name": key[1], "family": key[2], "severity": key[3], "total": len(rows)}
        for key, rows in by_plugin.items()
    ]
    software_rows.sort(key=lambda item: (-SEVERITY_WEIGHT.get(item["severity"], 0), -item["total"], item["plugin_id"]))
    return asset_rows, software_rows, evidence


def build_attack_vectors(findings: Iterable[NormalizedFinding]) -> list[dict[str, Any]]:
    rows = tuple(
        item for item in findings
        if item.state in OPEN_STATES and item.exploitable is True and item.cvss_attack_vector
    )
    labels = {"LOCAL": "local", "NETWORK": "network", "ADJACENT_NETWORK": "adjacent_network", "PHYSICAL": "physical"}
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    for finding in rows:
        column = labels[finding.cvss_attack_vector]
        groups["Exploitable"][column] += 1
        for framework in finding.exploit_frameworks:
            groups[framework][column] += 1
    order = ["Exploitable", *sorted((name for name in groups if name != "Exploitable"), key=str.casefold)]
    return [
        {"framework": name, **{column: groups[name][column] for column in labels.values()}}
        for name in order if name in groups
    ]


def build_was_unsupported(
    findings: Iterable[NormalizedWasFinding],
    catalog: UnsupportedCatalog,
    period: ReportingPeriod | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    grouped: dict[tuple[int, str, str, str], list[NormalizedWasFinding]] = defaultdict(list)
    evidence: list[dict[str, str]] = []
    for finding in findings:
        if finding.state not in OPEN_STATES or (
            period is not None and not period.contains(parse_utc(finding.last_found_at))
        ):
            continue
        match = _explicit_unsupported(finding, catalog)
        if not match:
            continue
        grouped[(finding.plugin_id, finding.plugin_name or "", finding.plugin_family or "", finding.severity)].append(finding)
        evidence.append({"finding_key": finding.finding_key, "field": match[0], "pattern": match[1]})
    rows = [
        {
            "plugin_id": key[0], "name": key[1], "family": key[2], "severity": key[3],
            "total": len(values),
            "applications": len({item.application_uri for item in values if item.application_uri}),
            "vpr": max((item.vpr_score for item in values if item.vpr_score is not None), default=None),
        }
        for key, values in grouped.items()
    ]
    rows.sort(key=lambda item: (-SEVERITY_WEIGHT.get(item["severity"], 0), -item["total"], item["plugin_id"]))
    return rows, evidence


def build_current_intelligence(
    *,
    assets: Iterable[NormalizedAsset],
    findings: Iterable[NormalizedFinding],
    was_findings: Iterable[NormalizedWasFinding],
    period: ReportingPeriod,
    open_collected: bool,
    fixed_collected: bool,
    was_collected: bool,
    catalog: UnsupportedCatalog | None = None,
) -> IntelligenceResult:
    asset_rows, finding_rows, was_rows = tuple(assets), tuple(findings), tuple(was_findings)
    catalog = catalog or load_unsupported_catalog()
    scan_health = build_scan_auth_health(asset_rows, period)
    plugin_family = build_plugin_family(finding_rows, period) if fixed_collected else []
    eol_assets, eol_software, eol_evidence = build_eol_data(finding_rows, asset_rows, catalog) if open_collected else ([], [], [])
    attack_vectors = build_attack_vectors(finding_rows) if open_collected else []
    was_unsupported, was_evidence = build_was_unsupported(
        was_rows, catalog, period
    ) if was_collected else ([], [])

    def status(collected: bool, present: bool) -> str:
        if not collected:
            return IntelligenceStatus.DATA_UNAVAILABLE.value
        return (IntelligenceStatus.AVAILABLE if present else IntelligenceStatus.NO_OCCURRENCES).value

    statuses = {
        "scan_auth_health": status(True, scan_health["total"] > 0),
        "vm_plugin_family": status(fixed_collected, bool(plugin_family)),
        "vm_eol_software": status(open_collected, bool(eol_software)),
        "vm_exploit_vector": status(open_collected, bool(attack_vectors)),
        "was_unsupported_tech": status(was_collected, bool(was_unsupported)),
    }
    return IntelligenceResult(
        data={
            "scan_auth_health": scan_health,
            "plugin_family": plugin_family,
            "eol_assets": eol_assets,
            "eol_software": eol_software,
            "attack_vectors": attack_vectors,
            "was_unsupported_tech": was_unsupported,
        },
        statuses=statuses,
        provenance={
            "period_id": period.period_id,
            "unsupported_catalog_version": catalog.version,
            "eol_matches": eol_evidence,
            "was_unsupported_matches": was_evidence,
            "rules": {
                "scan_auth_health": "last_scan_at and last_authenticated_scan_at within report period",
                "vm_plugin_family": "state=FIXED and last_fixed_at within report period",
                "vm_exploit_vector": "open actionable exploitable findings with explicit CVSS attack vector",
            },
        },
    )
