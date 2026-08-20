from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from tenable_reports.domain.reporting import ReportingPeriod, parse_utc


ACTIONABLE_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
OPEN_STATES = frozenset({"OPEN", "REOPENED"})
SEVERITY_WEIGHT = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}


@dataclass(frozen=True, slots=True)
class NormalizedWasFinding:
    finding_key: str
    client_id: str
    source_finding_id: str | None
    source_asset_id: str
    application_uri: str | None
    affected_uri: str | None
    plugin_id: int
    plugin_name: str | None
    plugin_family: str | None
    cves: tuple[str, ...]
    references: tuple[str, ...]
    owasp_2021: tuple[str, ...]
    synopsis: str | None
    description: str | None
    solution: str | None
    output: str | None
    proof: str | None
    payload: str | None
    http_method: str | None
    input_type: str | None
    input_name: str | None
    state: str
    severity: str
    first_found_at: str | None
    last_found_at: str | None
    last_fixed_at: str | None
    indexed_at: str | None
    cvss3_base_score: float | None
    vpr_score: float | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("cves", "references", "owasp_2021"):
            data[key] = list(data[key])
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NormalizedWasFinding":
        values = dict(data)
        for key in ("cves", "references", "owasp_2021"):
            values[key] = tuple(values.get(key) or ())
        return cls(**values)


@dataclass(frozen=True, slots=True)
class WasNormalizationResult:
    findings: tuple[NormalizedWasFinding, ...]
    raw_records: int
    rejected_records: int
    duplicate_records: int

    def validate(self) -> None:
        if len(self.findings) + self.rejected_records + self.duplicate_records != self.raw_records:
            raise ValueError("Reconciliacao da normalizacao WAS inconsistente.")


def _path(record: Mapping[str, Any], dotted: str) -> Any:
    current: Any = record
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _first(record: Mapping[str, Any], paths: Sequence[str]) -> Any:
    for path in paths:
        value = _path(record, path)
        if value is not None and value != "" and value != []:
            return value
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _strings(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return tuple(sorted({str(item).strip() for item in values if item is not None and str(item).strip()}, key=str.casefold))


def _application_uri(url: str | None, fqdn: str | None) -> str | None:
    if url:
        parsed = urlsplit(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return fqdn


def normalize_was_findings(
    records: Iterable[Mapping[str, Any]], *, client_id: str
) -> WasNormalizationResult:
    findings: list[NormalizedWasFinding] = []
    seen: set[str] = set()
    raw_count = 0
    rejected = 0
    duplicates = 0
    for record in records:
        raw_count += 1
        asset_id = _text(_first(record, ("asset.uuid", "asset.id")))
        plugin_id = _integer(_first(record, ("plugin.id", "definition.id")))
        if not asset_id or plugin_id is None:
            rejected += 1
            continue
        source_id = _text(_first(record, ("finding_id", "id")))
        affected_uri = _text(_first(record, ("url", "uri")))
        identity = source_id or "|".join((asset_id, str(plugin_id), affected_uri or "", _text(record.get("http_method")) or ""))
        key = "tenable_was:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        findings.append(NormalizedWasFinding(
            finding_key=key,
            client_id=client_id,
            source_finding_id=source_id,
            source_asset_id=asset_id,
            application_uri=_application_uri(affected_uri, _text(_first(record, ("asset.fqdn", "asset.name")))),
            affected_uri=affected_uri,
            plugin_id=plugin_id,
            plugin_name=_text(_first(record, ("plugin.name", "definition.name"))),
            plugin_family=_text(_first(record, ("plugin.family", "definition.family"))),
            cves=_strings(_first(record, ("plugin.cve", "definition.cve"))),
            references=_strings(_first(record, ("plugin.see_also", "definition.see_also"))),
            owasp_2021=_strings(_first(record, ("plugin.owasp_2021", "definition.owasp_2021"))),
            synopsis=_text(_first(record, ("plugin.synopsis", "definition.synopsis"))),
            description=_text(_first(record, ("plugin.description", "definition.description"))),
            solution=_text(_first(record, ("plugin.solution", "definition.solution"))),
            output=_text(record.get("output")),
            proof=_text(record.get("proof")),
            payload=_text(record.get("payload")),
            http_method=_text(record.get("http_method")),
            input_type=_text(record.get("input_type")),
            input_name=_text(record.get("input_name")),
            state=(_text(record.get("state")) or "UNKNOWN").upper(),
            severity=(_text(record.get("severity")) or "UNKNOWN").upper(),
            first_found_at=_text(record.get("first_found")),
            last_found_at=_text(_first(record, ("last_found", "last_observed"))),
            last_fixed_at=_text(record.get("last_fixed")),
            indexed_at=_text(record.get("indexed_at")),
            cvss3_base_score=_number(_first(record, ("plugin.cvss3_base_score", "definition.cvss3_base_score"))),
            vpr_score=_number(_first(record, ("plugin.vpr_v2.score", "plugin.vpr.score", "definition.vpr.score"))),
        ))
    result = WasNormalizationResult(tuple(findings), raw_count, rejected, duplicates)
    result.validate()
    return result


def _in_period(finding: NormalizedWasFinding, period: ReportingPeriod) -> bool:
    event = parse_utc(
        finding.last_fixed_at if finding.state == "FIXED" else finding.last_found_at
    )
    return period.contains(event)


def build_was_report_data(
    findings: Iterable[NormalizedWasFinding],
    *,
    period: ReportingPeriod,
    collected: bool,
    include_info_severity: bool,
    top_limit: int,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], dict[str, int]]:
    rows = tuple(findings)
    included = tuple(
        item for item in rows
        if item.state in OPEN_STATES | {"FIXED"}
        and item.severity in set(ACTIONABLE_SEVERITIES) | ({"INFO"} if include_info_severity else set())
        and _in_period(item, period)
    )
    open_rows = tuple(item for item in included if item.state in OPEN_STATES)
    apps: dict[str, list[NormalizedWasFinding]] = defaultdict(list)
    for item in open_rows:
        apps[item.source_asset_id].append(item)
    application_rows: list[dict[str, Any]] = []
    for asset_id, instances in apps.items():
        counts = Counter(item.severity for item in instances)
        application_rows.append({
            "source_asset_id": asset_id,
            "uri": next((item.application_uri for item in instances if item.application_uri), None),
            **{severity.lower(): counts.get(severity, 0) for severity in ACTIONABLE_SEVERITIES},
            "total": len(instances),
        })
    application_rows.sort(key=lambda item: (-item["total"], -item["critical"], -item["high"], item["source_asset_id"]))

    plugins: dict[int, list[NormalizedWasFinding]] = defaultdict(list)
    for item in open_rows:
        plugins[item.plugin_id].append(item)
    top_rows: list[dict[str, Any]] = []
    for plugin_id, instances in plugins.items():
        severity = max((item.severity for item in instances), key=lambda value: SEVERITY_WEIGHT.get(value, -1))
        vpr = [item.vpr_score for item in instances if item.vpr_score is not None]
        refs = {url for item in instances for url in item.references if url.startswith(("https://", "http://"))}
        for cve in {cve for item in instances for cve in item.cves}:
            if cve.upper().startswith("CVE-"):
                refs.add(f"https://www.cve.org/CVERecord?id={cve}")
        applications = [{
            "source_asset_id": item.source_asset_id,
            "uri": item.affected_uri or item.application_uri,
            "http_method": item.http_method,
            "input_type": item.input_type,
            "input_name": item.input_name,
            "plugin_output": item.output,
        } for item in sorted(instances, key=lambda value: (value.source_asset_id, value.affected_uri or "", value.finding_key))]
        top_rows.append({
            "plugin_id": plugin_id,
            "plugin_name": max((item.plugin_name or "" for item in instances), key=len) or None,
            "plugin_family": max((item.plugin_family or "" for item in instances), key=len) or None,
            "severity": severity,
            "vpr_score": max(vpr) if vpr else None,
            "affected_assets": len({item.source_asset_id for item in instances}),
            "finding_instances": len(instances),
            "reference_urls": sorted(refs),
            "synopsis": max((item.synopsis or "" for item in instances), key=len) or None,
            "description": max((item.description or "" for item in instances), key=len) or None,
            "solution": max((item.solution or "" for item in instances), key=len) or None,
            "applications": applications,
        })
    top_rows.sort(key=lambda item: (
        -(item["vpr_score"] if item["vpr_score"] is not None else -1),
        -SEVERITY_WEIGHT.get(item["severity"], -1),
        -item["finding_instances"],
        item["plugin_id"],
    ))
    owasp: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for item in open_rows:
        for label in item.owasp_2021:
            match = re.search(r"\bA0?(10|[1-9])\b", label.upper())
            if not match:
                continue
            category = f"A{int(match.group(1)):02d}"
            row = owasp[category].setdefault(item.plugin_id, {
                "plugin_id": item.plugin_id,
                "name": item.plugin_name,
                "instances": 0,
            })
            row["instances"] += 1
    availability = "AVAILABLE" if collected and rows else "NO_DATA" if collected else "NOT_COLLECTED"
    was = {
        "availability": availability,
        "applications": application_rows,
        "top_vulnerabilities": top_rows[:top_limit],
        "owasp": {
            category: sorted(values.values(), key=lambda item: (-item["instances"], item["plugin_id"]))
            for category, values in sorted(owasp.items())
        },
        "population": {
            "input": len(rows),
            "included": len(included),
            "open": len(open_rows),
            "fixed": sum(item.state == "FIXED" for item in included),
            "excluded": len(rows) - len(included),
        },
    }
    return was, tuple(top_rows[:top_limit]), was["population"]
