from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence

from tenable_reports.domain.normalization import (
    DataQualityIssue,
    NormalizedAsset,
    NormalizedFinding,
    QualitySeverity,
)


SOURCE = "tenable_inventory_findings"
STATE_MAP = {
    "ACTIVE": "OPEN",
    "RESURFACED": "REOPENED",
    "FIXED": "FIXED",
}


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


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().casefold()
    if normalized in {"true", "yes", "1", "available"}:
        return True
    if normalized in {"false", "no", "0", "not_available"}:
        return False
    return None


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    values: set[str] = set()
    for raw in raw_values:
        if raw is None:
            continue
        for item in re.split(r"\s*[,;]\s*", str(raw).strip()):
            if item:
                values.add(item)
    return tuple(sorted(values, key=str.casefold))


def _attack_vector(record: Mapping[str, Any]) -> str | None:
    vector = _text(_first(record, ("cvss4_vector", "cvss3_vector", "cvss_vector")))
    if not vector:
        return None
    match = re.search(r"(?:^|/)AV:([NALP])(?:/|$)", vector.upper())
    return {
        "N": "NETWORK",
        "A": "ADJACENT_NETWORK",
        "L": "LOCAL",
        "P": "PHYSICAL",
    }.get(match.group(1)) if match else None


def _finding_key(
    *,
    detection_id: str | None,
    asset_id: str,
    finding_name: str,
    port: int,
    protocol: str,
) -> str:
    # O ID da deteccao identifica a ocorrencia no Inventory; ele nunca e tratado
    # como plugin ID. O fingerprint protege a identidade caso esse ID beta mude.
    canonical = "|".join((
        detection_id or "",
        asset_id,
        finding_name.casefold(),
        str(port),
        protocol.casefold(),
    ))
    return f"tenable_inventory:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def normalize_inventory_findings(
    records: Iterable[Mapping[str, Any]],
    *,
    client_id: str,
    assets_by_id: Mapping[str, NormalizedAsset],
) -> tuple[tuple[NormalizedFinding, ...], tuple[DataQualityIssue, ...], int]:
    findings: list[NormalizedFinding] = []
    issues: list[DataQualityIssue] = []
    rejected = 0
    seen_keys: set[str] = set()

    for index, record in enumerate(records):
        detection_id = _text(_first(record, (
            "finding_detection_id", "finding.id", "detection_id",
        )))
        asset_id = _text(_first(record, (
            "asset_id", "asset.id", "asset_uuid", "asset.uuid",
        )))
        finding_name = _text(_first(record, (
            "finding_name", "name", "definition.name",
        )))

        if not asset_id:
            rejected += 1
            issues.append(DataQualityIssue(
                code="INVENTORY_ASSET_ID_MISSING",
                severity=QualitySeverity.ERROR,
                source=SOURCE,
                record_index=index,
                source_id=detection_id,
                message="Finding Inventory rejeitado porque o identificador do ativo esta ausente.",
            ))
            continue
        if not finding_name:
            rejected += 1
            issues.append(DataQualityIssue(
                code="INVENTORY_FINDING_NAME_MISSING",
                severity=QualitySeverity.ERROR,
                source=SOURCE,
                record_index=index,
                source_id=detection_id,
                message="Finding Inventory rejeitado porque o nome da deteccao esta ausente.",
            ))
            continue

        if not detection_id:
            issues.append(DataQualityIssue(
                code="INVENTORY_FINDING_DETECTION_ID_MISSING",
                severity=QualitySeverity.WARNING,
                source=SOURCE,
                record_index=index,
                source_id=None,
                message="ID da deteccao ausente; a identidade usa apenas o fingerprint estavel.",
            ))

        port = _integer(_first(record, ("port", "port.port")))
        protocol = (_text(_first(record, ("protocol", "port.protocol"))) or "unknown").lower()
        if port is None:
            port = 0
            issues.append(DataQualityIssue(
                code="INVENTORY_FINDING_PORT_MISSING",
                severity=QualitySeverity.WARNING,
                source=SOURCE,
                record_index=index,
                source_id=detection_id,
                message="Porta ausente; a identidade usa o sentinela 0.",
            ))

        plugin_id = _integer(_first(record, (
            "plugin_id", "nessus_plugin_id", "plugin.id", "definition.id",
        )))
        if plugin_id is None:
            issues.append(DataQualityIssue(
                code="INVENTORY_PLUGIN_ID_UNRESOLVED",
                severity=QualitySeverity.WARNING,
                source=SOURCE,
                record_index=index,
                source_id=detection_id,
                message="Plugin ID ainda nao foi resolvido pelo catalogo; o ID da deteccao nao foi reutilizado.",
            ))

        key = _finding_key(
            detection_id=detection_id,
            asset_id=asset_id,
            finding_name=finding_name,
            port=port,
            protocol=protocol,
        )
        if key in seen_keys:
            rejected += 1
            issues.append(DataQualityIssue(
                code="INVENTORY_FINDING_KEY_DUPLICATE",
                severity=QualitySeverity.ERROR,
                source=SOURCE,
                record_index=index,
                source_id=detection_id,
                message="Finding Inventory duplicado na mesma identidade foi rejeitado.",
            ))
            continue
        seen_keys.add(key)

        asset = assets_by_id.get(asset_id)
        if asset is None:
            issues.append(DataQualityIssue(
                code="INVENTORY_FINDING_ASSET_ORPHAN",
                severity=QualitySeverity.ERROR,
                source=SOURCE,
                record_index=index,
                source_id=detection_id,
                message="Finding Inventory nao encontrou o ativo exportado pelo identificador estavel.",
            ))

        state_value = (_text(record.get("state")) or "UNKNOWN").upper()
        normalized_state = STATE_MAP.get(state_value, state_value)
        findings.append(NormalizedFinding(
            finding_key=key,
            client_id=client_id,
            source=SOURCE,
            source_finding_id=detection_id,
            source_asset_id=asset_id,
            asset_key=asset.asset_key if asset else None,
            plugin_id=plugin_id,
            plugin_name=finding_name,
            plugin_family=_text(_first(record, ("plugin_family", "family", "definition.family"))),
            cves=_strings(_first(record, ("cves", "cve", "definition.cve"))),
            references=_strings(_first(record, ("references", "see_also", "definition.references"))),
            synopsis=_text(_first(record, ("synopsis", "definition.synopsis"))),
            description=_text(_first(record, ("description", "definition.description"))),
            solution=_text(_first(record, ("solution", "definition.solution"))),
            cvss2_base_score=_number(_first(record, ("cvss2_base_score", "cvss_base_score"))),
            cvss3_base_score=_number(_first(record, ("cvss3_base_score", "cvss3.base_score"))),
            has_patch=_boolean(_first(record, ("has_patch", "patch_available"))),
            plugin_output=_text(_first(record, ("output", "plugin_output"))),
            port=port,
            protocol=protocol,
            service=_text(_first(record, ("service", "port.service"))),
            state=normalized_state,
            severity=(_text(record.get("severity")) or "UNKNOWN").upper(),
            first_found_at=_text(_first(record, ("first_observed_at", "first_observed", "first_found"))),
            last_found_at=_text(_first(record, ("last_observed_at", "last_observed", "last_found"))),
            last_fixed_at=_text(_first(record, ("last_fixed_at", "last_fixed"))),
            resurfaced_at=_text(_first(record, ("resurfaced_at", "resurfaced_date"))),
            exploitable=_boolean(_first(record, ("exploit_available", "exploitable"))),
            vpr_score=_number(_first(record, ("vpr_score", "vpr.score"))),
            exploit_frameworks=_strings(_first(record, ("exploit_frameworks",))),
            cvss_attack_vector=_attack_vector(record),
        ))

    return tuple(findings), tuple(issues), rejected
