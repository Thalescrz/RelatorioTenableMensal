from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

from tenable_reports.domain.normalization import (
    DataQualityIssue,
    NormalizedFinding,
    QualitySeverity,
)


def normalize_plugin_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.casefold().split())


def _path(record: Mapping[str, Any], dotted: str) -> Any:
    current: Any = record
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _first(record: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        value = _path(record, path)
        if value is not None and value != "" and value != []:
            return value
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


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
    items = value if isinstance(value, (list, tuple, set)) else [value]
    result: set[str] = set()
    for raw in items:
        if raw is None:
            continue
        for item in re.split(r"\s*[,;]\s*", str(raw).strip()):
            if item:
                result.add(item)
    return tuple(sorted(result, key=str.casefold))


def _frameworks(record: Mapping[str, Any]) -> tuple[str, ...]:
    values = set(_strings(_first(
        record, "plugin.exploit_frameworks", "definition.exploit_frameworks"
    )))
    for label, field in (
        ("Canvas", "canvas"),
        ("Core Impact", "core"),
        ("D2 Elliot", "d2_elliot"),
        ("ExploitHub", "exploithub"),
        ("Metasploit", "metasploit"),
    ):
        if _boolean(_first(
            record,
            f"plugin.exploit_framework_{field}",
            f"definition.exploit_framework_{field}",
            f"definition.{field}",
        )) is True:
            values.add(label)
    return tuple(sorted(values, key=str.casefold))


@dataclass(frozen=True, slots=True)
class PluginCatalogEntry:
    client_id: str
    tenant_id: str
    plugin_id: int
    name: str | None
    normalized_name: str
    family: str | None
    synopsis: str | None
    description: str | None
    solution: str | None
    references: tuple[str, ...]
    cves: tuple[str, ...]
    cvss2_base_score: float | None
    cvss3_base_score: float | None
    vpr_score: float | None
    exploitable: bool | None
    exploit_frameworks: tuple[str, ...]
    provenance: Mapping[str, Any]
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "plugin_id": self.plugin_id,
            "name": self.name,
            "normalized_name": self.normalized_name,
            "family": self.family,
            "synopsis": self.synopsis,
            "description": self.description,
            "solution": self.solution,
            "references": self.references,
            "cves": self.cves,
            "cvss2_base_score": self.cvss2_base_score,
            "cvss3_base_score": self.cvss3_base_score,
            "vpr_score": self.vpr_score,
            "exploitable": self.exploitable,
            "exploit_frameworks": self.exploit_frameworks,
            "provenance": dict(self.provenance),
            "observed_at": self.observed_at,
        }


class PluginCatalogRepository(Protocol):
    def upsert(self, entries: Sequence[PluginCatalogEntry]) -> int: ...

    def find_by_normalized_name(
        self,
        *,
        client_id: str,
        tenant_id: str,
        name: str,
    ) -> tuple[PluginCatalogEntry, ...]: ...


class MemoryPluginCatalogRepository:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, int], PluginCatalogEntry] = {}

    @property
    def count(self) -> int:
        return len(self._entries)

    def upsert(self, entries: Sequence[PluginCatalogEntry]) -> int:
        for entry in entries:
            self._entries[(entry.client_id, entry.tenant_id, entry.plugin_id)] = entry
        return len(entries)

    def find_by_normalized_name(
        self,
        *,
        client_id: str,
        tenant_id: str,
        name: str,
    ) -> tuple[PluginCatalogEntry, ...]:
        normalized = normalize_plugin_name(name)
        return tuple(sorted(
            (
                item for item in self._entries.values()
                if item.client_id == client_id
                and item.tenant_id == tenant_id
                and item.normalized_name == normalized
            ),
            key=lambda item: item.plugin_id,
        ))


def build_plugin_catalog_entries(
    records: Iterable[Mapping[str, Any]],
    *,
    client_id: str,
    tenant_id: str,
    source: str,
    observed_at: str | None = None,
) -> tuple[PluginCatalogEntry, ...]:
    timestamp = observed_at or datetime.now(timezone.utc).isoformat()
    entries: dict[int, PluginCatalogEntry] = {}
    for record in records:
        plugin_id = _integer(_first(record, "plugin.id", "definition.id"))
        if plugin_id is None:
            continue
        name = _text(_first(record, "plugin.name", "definition.name"))
        direct_exploitable = _boolean(_first(
            record, "plugin.exploit_available", "definition.exploit_available"
        ))
        frameworks = _frameworks(record)
        entries[plugin_id] = PluginCatalogEntry(
            client_id=client_id,
            tenant_id=tenant_id,
            plugin_id=plugin_id,
            name=name,
            normalized_name=normalize_plugin_name(name),
            family=_text(_first(record, "plugin.family", "definition.family")),
            synopsis=_text(_first(record, "plugin.synopsis", "definition.synopsis")),
            description=_text(_first(record, "plugin.description", "definition.description")),
            solution=_text(_first(record, "plugin.solution", "definition.solution")),
            references=tuple(sorted(set(
                _strings(_first(record, "plugin.see_also", "definition.see_also"))
                + _strings(_first(record, "plugin.xrefs", "definition.references"))
            ), key=str.casefold)),
            cves=_strings(_first(record, "plugin.cve", "definition.cve")),
            cvss2_base_score=_number(_first(
                record, "plugin.cvss_base_score", "definition.cvss_base_score"
            )),
            cvss3_base_score=_number(_first(
                record, "plugin.cvss3_base_score", "definition.cvss3_base_score"
            )),
            vpr_score=_number(_first(record, "plugin.vpr.score", "definition.vpr.score")),
            exploitable=(direct_exploitable if direct_exploitable is not None else (True if frameworks else None)),
            exploit_frameworks=frameworks,
            provenance={
                "source": source,
                "source_finding_id": _text(_first(record, "finding_id", "id")),
            },
            observed_at=timestamp,
        )
    return tuple(entries[key] for key in sorted(entries))


def enrich_inventory_findings(
    findings: Iterable[NormalizedFinding],
    *,
    client_id: str,
    tenant_id: str,
    repository: PluginCatalogRepository,
) -> tuple[tuple[NormalizedFinding, ...], tuple[DataQualityIssue, ...]]:
    enriched: list[NormalizedFinding] = []
    issues: list[DataQualityIssue] = []
    for index, finding in enumerate(findings):
        if finding.plugin_id is not None:
            enriched.append(finding)
            continue
        matches = repository.find_by_normalized_name(
            client_id=client_id,
            tenant_id=tenant_id,
            name=finding.plugin_name or "",
        )
        if len(matches) != 1:
            code = "PLUGIN_METADATA_MISSING" if not matches else "PLUGIN_METADATA_AMBIGUOUS"
            message = (
                "Catalogo nao possui metadados univocos para o finding Inventory."
                if not matches
                else "Mais de um Plugin ID corresponde ao nome do finding Inventory."
            )
            issues.append(DataQualityIssue(
                code=code,
                severity=QualitySeverity.WARNING,
                source=finding.source,
                record_index=index,
                source_id=finding.source_finding_id,
                message=message,
            ))
            enriched.append(finding)
            continue

        match = matches[0]
        enriched.append(replace(
            finding,
            plugin_id=match.plugin_id,
            plugin_name=finding.plugin_name or match.name,
            plugin_family=finding.plugin_family or match.family,
            cves=tuple(sorted(set(finding.cves + match.cves), key=str.casefold)),
            references=tuple(sorted(
                set(finding.references + match.references), key=str.casefold
            )),
            synopsis=finding.synopsis or match.synopsis,
            description=finding.description or match.description,
            solution=finding.solution or match.solution,
            cvss2_base_score=(
                finding.cvss2_base_score
                if finding.cvss2_base_score is not None else match.cvss2_base_score
            ),
            cvss3_base_score=(
                finding.cvss3_base_score
                if finding.cvss3_base_score is not None else match.cvss3_base_score
            ),
            exploitable=(
                finding.exploitable
                if finding.exploitable is not None else match.exploitable
            ),
            vpr_score=(
                finding.vpr_score if finding.vpr_score is not None else match.vpr_score
            ),
            exploit_frameworks=tuple(sorted(
                set(finding.exploit_frameworks + match.exploit_frameworks),
                key=str.casefold,
            )),
        ))
    return tuple(enriched), tuple(issues)
