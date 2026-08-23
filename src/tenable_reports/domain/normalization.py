from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence


class AssetLifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"
    TERMINATED = "TERMINATED"


class QualitySeverity(StrEnum):
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    code: str
    severity: QualitySeverity
    source: str
    record_index: int
    message: str
    source_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data


@dataclass(frozen=True, slots=True)
class NormalizedAsset:
    asset_key: str
    client_id: str
    source: str
    source_asset_id: str
    lifecycle: AssetLifecycle
    display_name: str | None
    asset_types: tuple[str, ...]
    source_names: tuple[str, ...]
    hostnames: tuple[str, ...]
    fqdns: tuple[str, ...]
    ipv4s: tuple[str, ...]
    ipv6s: tuple[str, ...]
    mac_addresses: tuple[str, ...]
    operating_systems: tuple[str, ...]
    network_id: str | None
    network_name: str | None
    first_scan_at: str | None
    last_scan_at: str | None
    last_authenticated_scan_at: str | None
    created_at: str | None
    updated_at: str | None
    deleted_at: str | None
    terminated_at: str | None
    acr_score: float | None
    aes_score: float | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["lifecycle"] = self.lifecycle.value
        for key, value in tuple(data.items()):
            if isinstance(value, tuple):
                data[key] = list(value)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NormalizedAsset":
        values = dict(data)
        values["lifecycle"] = AssetLifecycle(str(values["lifecycle"]))
        for key in (
            "asset_types", "source_names", "hostnames", "fqdns", "ipv4s", "ipv6s",
            "mac_addresses", "operating_systems",
        ):
            values[key] = tuple(values.get(key) or ())
        return cls(**values)


@dataclass(frozen=True, slots=True)
class NormalizedFinding:
    finding_key: str
    client_id: str
    source: str
    source_finding_id: str | None
    source_asset_id: str
    asset_key: str | None
    plugin_id: int | None
    plugin_name: str | None
    plugin_family: str | None
    cves: tuple[str, ...]
    references: tuple[str, ...]
    synopsis: str | None
    description: str | None
    solution: str | None
    cvss2_base_score: float | None
    cvss3_base_score: float | None
    has_patch: bool | None
    plugin_output: str | None
    port: int
    protocol: str
    service: str | None
    state: str
    severity: str
    first_found_at: str | None
    last_found_at: str | None
    last_fixed_at: str | None
    resurfaced_at: str | None
    exploitable: bool | None
    vpr_score: float | None
    exploit_frameworks: tuple[str, ...] = ()
    cvss_attack_vector: str | None = None

    @property
    def is_orphan(self) -> bool:
        return self.asset_key is None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cves"] = list(self.cves)
        data["references"] = list(self.references)
        data["exploit_frameworks"] = list(self.exploit_frameworks)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NormalizedFinding":
        values = dict(data)
        values["cves"] = tuple(values.get("cves") or ())
        # Compatibilidade com snapshots publicados antes deste campo.
        values["references"] = tuple(values.get("references") or ())
        values["exploit_frameworks"] = tuple(values.get("exploit_frameworks") or ())
        values.setdefault("cvss_attack_vector", None)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class Reconciliation:
    raw_asset_records: int
    normalized_assets: int
    rejected_asset_records: int
    duplicate_asset_records: int
    raw_finding_records: int
    normalized_findings: int
    rejected_finding_records: int
    linked_findings: int
    orphan_findings: int

    def validate(self) -> None:
        if self.normalized_assets + self.rejected_asset_records + self.duplicate_asset_records != self.raw_asset_records:
            raise ValueError("Reconciliacao de ativos inconsistente.")
        if self.normalized_findings + self.rejected_finding_records != self.raw_finding_records:
            raise ValueError("Reconciliacao de findings inconsistente.")
        if self.linked_findings + self.orphan_findings != self.normalized_findings:
            raise ValueError("Reconciliacao do vinculo finding-asset inconsistente.")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    assets: tuple[NormalizedAsset, ...]
    findings: tuple[NormalizedFinding, ...]
    issues: tuple[DataQualityIssue, ...]
    reconciliation: Reconciliation


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
    text = str(value).strip()
    return text or None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, (list, tuple, set)) else [value]
    normalized = {str(item).strip() for item in values if str(item).strip()}
    return tuple(sorted(normalized, key=str.casefold))


def _combined_string_tuple(
    record: Mapping[str, Any],
    paths: Sequence[str],
) -> tuple[str, ...]:
    values: set[str] = set()
    for path in paths:
        values.update(_string_tuple(_path(record, path)))
    return tuple(sorted(values, key=str.casefold))


def _source_names(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        return _string_tuple(value.keys())
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                name = item.get("name") or item.get("type") or item.get("source")
                if name:
                    names.append(str(name))
            elif item:
                names.append(str(item))
        return _string_tuple(names)
    return _string_tuple(value)


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


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().upper()
    if text in {"TRUE", "YES", "1", "AVAILABLE"}:
        return True
    if text in {"FALSE", "NO", "0", "NOT_AVAILABLE", "NOT REQUIRED", "NOT_REQUIRED"}:
        return False
    return None


def _exploitability(record: Mapping[str, Any]) -> bool | None:
    direct = _bool_or_none(_first(
        record,
        ("plugin.exploit_available", "definition.exploit_available"),
    ))
    if direct is not None:
        return direct
    ease = (_text(_first(record, (
        "plugin.exploitability_ease",
        "definition.exploitability_ease",
    ))) or "").casefold()
    if ease == "exploits are available":
        return True
    if ease == "no known exploits are available":
        return False
    frameworks = _exploit_frameworks(record)
    return True if frameworks else None


EXPLOIT_FRAMEWORK_FIELDS = (
    ("Canvas", "exploit_framework_canvas", "canvas"),
    ("Core Impact", "exploit_framework_core", "core"),
    ("D2 Elliot", "exploit_framework_d2_elliot", "elliot"),
    ("ExploitHub", "exploit_framework_exploithub", "exploithub"),
    ("Metasploit", "exploit_framework_metasploit", "metasploit"),
)


def _exploit_frameworks(record: Mapping[str, Any]) -> tuple[str, ...]:
    names = set(_string_tuple(_first(record, (
        "plugin.exploit_frameworks", "definition.exploit_frameworks"
    ))))
    for label, legacy_field, official_field in EXPLOIT_FRAMEWORK_FIELDS:
        value = _first(record, (
            f"plugin.{legacy_field}",
            f"definition.{legacy_field}",
            f"definition.{official_field}",
        ))
        if _bool_or_none(value) is True:
            names.add(label)
    return tuple(sorted(names, key=str.casefold))


CVSS_ATTACK_VECTORS = {
    "N": "NETWORK",
    "A": "ADJACENT_NETWORK",
    "L": "LOCAL",
    "P": "PHYSICAL",
}


def _cvss_attack_vector(record: Mapping[str, Any]) -> str | None:
    vector = _text(_first(record, (
        "plugin.cvss4_vector", "definition.cvss4_vector",
        "definition.cvss4.base_vector",
        "plugin.cvss3_vector", "definition.cvss3_vector",
        "definition.cvss3.base_vector",
    )))
    if not vector:
        return None
    match = re.search(r"(?:^|/)AV:([NALP])(?:/|$)", vector.upper())
    return CVSS_ATTACK_VECTORS.get(match.group(1)) if match else None


def _normalized_ips(value: Any, version: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    valid: set[str] = set()
    invalid: set[str] = set()
    for raw in _string_tuple(value):
        try:
            parsed = ipaddress.ip_address(raw)
        except ValueError:
            invalid.add(raw)
            continue
        if parsed.version == version:
            valid.add(str(parsed))
        else:
            invalid.add(raw)
    return tuple(sorted(valid)), tuple(sorted(invalid))


def _asset_key(client_id: str, source_asset_id: str) -> str:
    return f"{client_id}:tenable_vm:{source_asset_id}"


def _finding_key(asset_id: str, plugin_id: int, port: int, protocol: str) -> str:
    canonical = f"{asset_id}|{plugin_id}|{port}|{protocol}"
    return "tenable_vm:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_assets(
    records: Iterable[Mapping[str, Any]],
    *,
    client_id: str,
) -> tuple[tuple[NormalizedAsset, ...], tuple[DataQualityIssue, ...], int, int]:
    assets: list[NormalizedAsset] = []
    issues: list[DataQualityIssue] = []
    seen: set[str] = set()
    rejected = 0
    duplicates = 0
    for index, record in enumerate(records):
        source_id = _text(_first(record, ("id", "uuid")))
        if not source_id:
            rejected += 1
            issues.append(DataQualityIssue(
                code="ASSET_ID_MISSING",
                severity=QualitySeverity.ERROR,
                source="tenable_vm_assets_v2",
                record_index=index,
                source_id=None,
                message="Ativo rejeitado porque id/uuid estavel esta ausente.",
            ))
            continue
        if source_id in seen:
            duplicates += 1
            issues.append(DataQualityIssue(
                code="ASSET_ID_DUPLICATE",
                severity=QualitySeverity.ERROR,
                source="tenable_vm_assets_v2",
                record_index=index,
                source_id=source_id,
                message="Registro duplicado de ativo nao foi mesclado por aliases.",
            ))
            continue
        seen.add(source_id)

        deleted_at = _text(_first(record, ("timestamps.deleted_at", "deleted_at")))
        terminated_at = _text(_first(record, ("timestamps.terminated_at", "terminated_at")))
        lifecycle = (
            AssetLifecycle.TERMINATED if terminated_at
            else AssetLifecycle.DELETED if deleted_at
            else AssetLifecycle.ACTIVE
        )
        ipv4s, invalid_ipv4 = _normalized_ips(
            _first(record, ("network.ipv4s", "ipv4_addresses", "ipv4")), 4
        )
        ipv6s, invalid_ipv6 = _normalized_ips(
            _first(record, ("network.ipv6s", "ipv6_addresses", "ipv6")), 6
        )
        for _invalid in (*invalid_ipv4, *invalid_ipv6):
            issues.append(DataQualityIssue(
                code="ASSET_IP_INVALID",
                severity=QualitySeverity.WARNING,
                source="tenable_vm_assets_v2",
                record_index=index,
                source_id=source_id,
                message="Endereco de rede invalido descartado.",
            ))
        assets.append(NormalizedAsset(
            asset_key=_asset_key(client_id, source_id),
            client_id=client_id,
            source="tenable_vm_assets_v2",
            source_asset_id=source_id,
            lifecycle=lifecycle,
            display_name=_text(_first(record, ("name", "display_name"))),
            asset_types=_string_tuple(_first(record, ("types", "type"))),
            source_names=_source_names(record.get("sources")),
            hostnames=_string_tuple(_first(record, ("network.hostnames", "hostname"))),
            fqdns=_string_tuple(_first(record, ("network.fqdns", "fqdn"))),
            ipv4s=ipv4s,
            ipv6s=ipv6s,
            mac_addresses=_string_tuple(_first(record, ("network.mac_addresses", "mac_address"))),
            operating_systems=_string_tuple(_first(record, ("operating_systems", "operating_system"))),
            network_id=_text(_first(record, (
                "network.network_id", "network.id", "network_id"
            ))),
            network_name=_text(_first(record, (
                "network.network_name", "network.name", "network_name"
            ))),
            first_scan_at=_text(_first(record, ("scan.first_scan_time", "first_scan_time"))),
            last_scan_at=_text(_first(record, ("scan.last_scan_time", "last_scan_time"))),
            last_authenticated_scan_at=_text(_first(record, (
                "scan.last_authenticated_scan_date", "last_authenticated_scan_date"
            ))),
            created_at=_text(_first(record, ("timestamps.created_at", "created_at"))),
            updated_at=_text(_first(record, ("timestamps.updated_at", "updated_at"))),
            deleted_at=deleted_at,
            terminated_at=terminated_at,
            acr_score=_number(_first(record, ("ratings.acr.score", "acr_score"))),
            aes_score=_number(_first(record, ("ratings.aes.score", "exposure_score"))),
        ))
    return tuple(assets), tuple(issues), rejected, duplicates


def normalize_findings(
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
        source_asset_id = _text(_first(record, ("asset.uuid", "asset.id")))
        plugin_id = _integer(_first(record, ("plugin.id", "definition.id")))
        if not source_asset_id or plugin_id is None:
            rejected += 1
            issues.append(DataQualityIssue(
                code="FINDING_IDENTITY_MISSING",
                severity=QualitySeverity.ERROR,
                source="tenable_vm_vulnerabilities",
                record_index=index,
                source_id=_text(_first(record, ("finding_id", "id"))),
                message="Finding rejeitado por ausencia de asset UUID ou plugin ID.",
            ))
            continue
        port = _integer(_first(record, ("port.port", "port")))
        protocol = (_text(_first(record, ("port.protocol", "protocol"))) or "unknown").lower()
        if port is None:
            port = 0
            issues.append(DataQualityIssue(
                code="FINDING_PORT_MISSING",
                severity=QualitySeverity.WARNING,
                source="tenable_vm_vulnerabilities",
                record_index=index,
                source_id=_text(_first(record, ("finding_id", "id"))),
                message="Porta ausente; identidade usa o sentinela 0.",
            ))
        key = _finding_key(source_asset_id, plugin_id, port, protocol)
        if key in seen_keys:
            rejected += 1
            issues.append(DataQualityIssue(
                code="FINDING_KEY_DUPLICATE",
                severity=QualitySeverity.ERROR,
                source="tenable_vm_vulnerabilities",
                record_index=index,
                source_id=_text(_first(record, ("finding_id", "id"))),
                message="Finding duplicado na mesma chave composta foi rejeitado.",
            ))
            continue
        seen_keys.add(key)
        asset = assets_by_id.get(source_asset_id)
        if asset is None:
            issues.append(DataQualityIssue(
                code="FINDING_ASSET_ORPHAN",
                severity=QualitySeverity.ERROR,
                source="tenable_vm_vulnerabilities",
                record_index=index,
                source_id=_text(_first(record, ("finding_id", "id"))),
                message="Finding nao encontrou asset.id correspondente; nao houve fallback por IP/hostname.",
            ))
        findings.append(NormalizedFinding(
            finding_key=key,
            client_id=client_id,
            source="tenable_vm_vulnerabilities",
            source_finding_id=_text(_first(record, ("finding_id", "id"))),
            source_asset_id=source_asset_id,
            asset_key=asset.asset_key if asset else None,
            plugin_id=plugin_id,
            plugin_name=_text(_first(record, ("plugin.name", "definition.name"))),
            plugin_family=_text(_first(record, ("plugin.family", "definition.family"))),
            cves=_string_tuple(_first(record, ("plugin.cve", "definition.cve"))),
            references=_combined_string_tuple(record, (
                "plugin.see_also",
                "plugin.xrefs",
                "definition.see_also",
                "definition.references",
            )),
            synopsis=_text(_first(record, ("plugin.synopsis", "definition.synopsis"))),
            description=_text(_first(record, ("plugin.description", "definition.description"))),
            solution=_text(_first(record, ("plugin.solution", "definition.solution"))),
            cvss2_base_score=_number(_first(record, (
                "plugin.cvss_base_score",
                "definition.cvss_base_score",
                "definition.cvss2.base_score",
            ))),
            cvss3_base_score=_number(_first(record, (
                "plugin.cvss3_base_score",
                "definition.cvss3_base_score",
                "definition.cvss3.base_score",
            ))),
            has_patch=(
                _bool_or_none(_first(
                    record, ("plugin.has_patch", "definition.has_patch")
                ))
                if _first(record, ("plugin.has_patch", "definition.has_patch"))
                is not None
                else (
                    True
                    if _first(record, (
                        "plugin.patch_publication_date",
                        "definition.patch_published",
                    ))
                    is not None
                    else None
                )
            ),
            plugin_output=_text(_first(record, ("output", "plugin_output"))),
            port=port,
            protocol=protocol,
            service=_text(_first(record, ("port.service", "service"))),
            state=(_text(record.get("state")) or "UNKNOWN").upper(),
            severity=(_text(record.get("severity")) or "UNKNOWN").upper(),
            first_found_at=_text(_first(record, ("first_found", "first_observed"))),
            last_found_at=_text(_first(record, ("last_found", "last_seen"))),
            last_fixed_at=_text(record.get("last_fixed")),
            resurfaced_at=_text(record.get("resurfaced_date")),
            exploitable=_exploitability(record),
            vpr_score=_number(_first(record, ("plugin.vpr.score", "definition.vpr.score"))),
            exploit_frameworks=_exploit_frameworks(record),
            cvss_attack_vector=_cvss_attack_vector(record),
        ))
    return tuple(findings), tuple(issues), rejected


def normalize_and_link(
    *,
    asset_records: Iterable[Mapping[str, Any]],
    finding_records: Iterable[Mapping[str, Any]],
    client_id: str,
) -> NormalizationResult:
    asset_count = 0

    def counted_assets() -> Iterable[Mapping[str, Any]]:
        nonlocal asset_count
        for record in asset_records:
            asset_count += 1
            yield record

    assets, asset_issues, rejected_assets, duplicate_assets = normalize_assets(
        counted_assets(), client_id=client_id
    )
    assets_by_id = {asset.source_asset_id: asset for asset in assets}
    finding_count = 0

    def counted_findings() -> Iterable[Mapping[str, Any]]:
        nonlocal finding_count
        for record in finding_records:
            finding_count += 1
            yield record

    findings, finding_issues, rejected_findings = normalize_findings(
        counted_findings(),
        client_id=client_id,
        assets_by_id=assets_by_id,
    )
    linked = sum(not finding.is_orphan for finding in findings)
    reconciliation = Reconciliation(
        raw_asset_records=asset_count,
        normalized_assets=len(assets),
        rejected_asset_records=rejected_assets,
        duplicate_asset_records=duplicate_assets,
        raw_finding_records=finding_count,
        normalized_findings=len(findings),
        rejected_finding_records=rejected_findings,
        linked_findings=linked,
        orphan_findings=len(findings) - linked,
    )
    reconciliation.validate()
    return NormalizationResult(
        assets=assets,
        findings=findings,
        issues=asset_issues + finding_issues,
        reconciliation=reconciliation,
    )
