from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from tenable_reports.domain.fingerprints import (
    FINGERPRINT_SIZE,
    FINGERPRINT_VERSION,
    fingerprint_finding_key,
)


HISTORY_SCHEMA_VERSION = 2
HISTORY_DEFINITION_VERSION = "history-definition-v1.0"
ACTIONABLE_SEVERITIES = ("critical", "high", "medium", "low")


@dataclass(frozen=True, slots=True)
class SnapshotCompatibility:
    client_id: str
    tenant_id: str
    execution_type: str
    period_mode: str
    timezone: str
    metric_definition_version: str
    scope_hash: str

    def to_dict(self) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "execution_type": self.execution_type,
            "period_mode": self.period_mode,
            "timezone": self.timezone,
            "metric_definition_version": self.metric_definition_version,
            "scope_hash": self.scope_hash,
        }


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    snapshot_id: str
    run_id: str
    period_id: str
    period_start_at: str
    period_end_at: str
    generated_at: str
    compatibility: SnapshotCompatibility
    summary: dict[str, Any]
    open_finding_keys: tuple[bytes, ...]
    fixed_finding_keys: tuple[bytes, ...]
    resurfaced_finding_keys: tuple[bytes, ...]
    network_tag_snapshots: tuple[dict[str, Any], ...]
    open_plugin_counts: tuple[dict[str, Any], ...] = ()
    source_dataset_path: str | None = None
    source_dataset_sha256: str | None = None
    schema_version: int = HISTORY_SCHEMA_VERSION
    history_definition_version: str = HISTORY_DEFINITION_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "open_finding_keys",
            "fixed_finding_keys",
            "resurfaced_finding_keys",
        ):
            normalized: set[bytes] = set()
            for raw in getattr(self, field_name):
                value = (
                    bytes(raw)
                    if isinstance(raw, (bytes, bytearray, memoryview))
                    else fingerprint_finding_key(str(raw))
                )
                if len(value) != FINGERPRINT_SIZE:
                    raise ValueError("Fingerprint histórico inválido.")
                normalized.add(value)
            object.__setattr__(self, field_name, tuple(sorted(normalized)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "history_definition_version": self.history_definition_version,
            "snapshot_id": self.snapshot_id,
            "run_id": self.run_id,
            "period_id": self.period_id,
            "period_start_at": self.period_start_at,
            "period_end_at": self.period_end_at,
            "generated_at": self.generated_at,
            "compatibility": self.compatibility.to_dict(),
            "summary": self.summary,
            "fingerprint_version": FINGERPRINT_VERSION,
            "open_finding_keys": [value.hex() for value in self.open_finding_keys],
            "fixed_finding_keys": [value.hex() for value in self.fixed_finding_keys],
            "resurfaced_finding_keys": [
                value.hex() for value in self.resurfaced_finding_keys
            ],
            "network_tag_snapshots": list(self.network_tag_snapshots),
            "open_plugin_counts": list(self.open_plugin_counts),
            "source_dataset_path": self.source_dataset_path,
            "source_dataset_sha256": self.source_dataset_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HistorySnapshot":
        compatibility = data.get("compatibility")
        if not isinstance(compatibility, Mapping):
            raise ValueError("Snapshot historico sem contrato de compatibilidade.")
        summary = data.get("summary")
        if not isinstance(summary, Mapping):
            raise ValueError("Snapshot historico sem resumo mensal.")
        fingerprint_version = str(data.get("fingerprint_version") or "")
        if fingerprint_version and fingerprint_version != FINGERPRINT_VERSION:
            raise ValueError(
                f"Versão de fingerprint histórico não suportada: {fingerprint_version}"
            )

        def fingerprints(field: str) -> tuple[bytes, ...]:
            values: list[bytes] = []
            for raw in data.get(field) or ():
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    value = bytes(raw)
                elif fingerprint_version == FINGERPRINT_VERSION:
                    try:
                        value = bytes.fromhex(str(raw))
                    except ValueError as exc:
                        raise ValueError("Fingerprint histórico inválido.") from exc
                else:
                    value = fingerprint_finding_key(str(raw))
                if len(value) != FINGERPRINT_SIZE:
                    raise ValueError("Fingerprint histórico inválido.")
                values.append(value)
            return tuple(sorted(set(values)))

        return cls(
            schema_version=int(data.get("schema_version") or HISTORY_SCHEMA_VERSION),
            history_definition_version=str(
                data.get("history_definition_version") or HISTORY_DEFINITION_VERSION
            ),
            snapshot_id=str(data.get("snapshot_id") or ""),
            run_id=str(data.get("run_id") or ""),
            period_id=str(data.get("period_id") or ""),
            period_start_at=str(data.get("period_start_at") or ""),
            period_end_at=str(data.get("period_end_at") or ""),
            generated_at=str(data.get("generated_at") or ""),
            compatibility=SnapshotCompatibility(
                client_id=str(compatibility.get("client_id") or ""),
                tenant_id=str(compatibility.get("tenant_id") or ""),
                execution_type=str(compatibility.get("execution_type") or ""),
                period_mode=str(compatibility.get("period_mode") or ""),
                timezone=str(compatibility.get("timezone") or ""),
                metric_definition_version=str(
                    compatibility.get("metric_definition_version") or ""
                ),
                scope_hash=str(compatibility.get("scope_hash") or ""),
            ),
            summary=dict(summary),
            open_finding_keys=fingerprints("open_finding_keys"),
            fixed_finding_keys=fingerprints("fixed_finding_keys"),
            resurfaced_finding_keys=fingerprints("resurfaced_finding_keys"),
            network_tag_snapshots=tuple(
                dict(value)
                for value in data.get("network_tag_snapshots") or ()
                if isinstance(value, Mapping)
            ),
            open_plugin_counts=tuple(
                dict(value)
                for value in data.get("open_plugin_counts") or ()
                if isinstance(value, Mapping)
            ),
            source_dataset_path=(
                str(data.get("source_dataset_path"))
                if data.get("source_dataset_path") else None
            ),
            source_dataset_sha256=(
                str(data.get("source_dataset_sha256"))
                if data.get("source_dataset_sha256") else None
            ),
        )


def snapshots_compatible(
    current: SnapshotCompatibility,
    candidate: SnapshotCompatibility,
) -> bool:
    return current == candidate


def _number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _severity_values(metric: Mapping[str, Any]) -> dict[str, int] | None:
    values = metric.get("by_severity")
    if not isinstance(values, Mapping):
        return None
    return {severity: int(values.get(severity) or 0) for severity in ACTIONABLE_SEVERITIES}


def summary_from_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("Dataset sem metricas para o historico.")
    non_mitigated = metrics.get("non_mitigated")
    mitigated = metrics.get("mitigated")
    resurfaced = metrics.get("resurfaced")
    if not isinstance(non_mitigated, Mapping):
        raise ValueError("Dataset sem metrica non_mitigated.")
    if not isinstance(mitigated, Mapping):
        mitigated = {}
    if not isinstance(resurfaced, Mapping):
        resurfaced = {}
    return {
        "non_mitigated": _number(non_mitigated.get("total")),
        "non_mitigated_by_severity": _severity_values(non_mitigated),
        "mitigated": _number(mitigated.get("total")),
        "mitigated_by_severity": _severity_values(mitigated),
        "new": _number(non_mitigated.get("new_in_period")),
        "new_by_severity": (
            {
                severity: int(non_mitigated["new_by_severity"].get(severity) or 0)
                for severity in ACTIONABLE_SEVERITIES
            }
            if isinstance(non_mitigated.get("new_by_severity"), Mapping) else None
        ),
        "resurfaced": _number(resurfaced.get("total")),
        "resurfaced_by_severity": _severity_values(resurfaced),
        "exploitable": _number(non_mitigated.get("exploitable")),
        "exploitable_by_severity": (
            {
                severity: int(non_mitigated["exploitable_by_severity"].get(severity) or 0)
                for severity in ACTIONABLE_SEVERITIES
            }
            if isinstance(non_mitigated.get("exploitable_by_severity"), Mapping) else None
        ),
        "patch_available_over_30_days": _number(
            non_mitigated.get("patch_available_over_30_days")
        ),
        "patch_available_over_30_days_by_severity": (
            {
                severity: int(
                    non_mitigated["patch_available_over_30_days_by_severity"].get(
                        severity
                    ) or 0
                )
                for severity in ACTIONABLE_SEVERITIES
            }
            if isinstance(
                non_mitigated.get("patch_available_over_30_days_by_severity"), Mapping
            ) else None
        ),
        "aging": non_mitigated.get("aging"),
        "aging_by_severity": non_mitigated.get("aging_by_severity"),
        "by_operating_system": metrics.get("by_operating_system"),
        "by_cvss": metrics.get("by_cvss"),
        "cvss_vpr_matrix": metrics.get("cvss_vpr_matrix"),
        "vpr_rating": metrics.get("vpr_rating"),
        "by_exploit_framework": metrics.get("by_exploit_framework"),
    }


def monthly_history_row(snapshot: HistorySnapshot, *, label: str) -> dict[str, Any]:
    summary = snapshot.summary
    row: dict[str, Any] = {
        "period_id": snapshot.period_id,
        "label": label,
    }
    for key in (
        "mitigated",
        "non_mitigated",
        "new",
        "resurfaced",
        "mitigated_by_severity",
        "non_mitigated_by_severity",
        "new_by_severity",
        "resurfaced_by_severity",
    ):
        if summary.get(key) is not None:
            row[key] = summary[key]
    return row


def previous_period_overview(snapshot: HistorySnapshot, *, label: str) -> dict[str, Any]:
    summary = snapshot.summary
    mitigated_by_severity = summary.get("mitigated_by_severity") or {}
    open_by_severity = summary.get("non_mitigated_by_severity") or {}
    exploitable_by_severity = summary.get("exploitable_by_severity") or {}
    patch_by_severity = summary.get("patch_available_over_30_days_by_severity") or {}
    result: dict[str, Any] = {
        "period_id": snapshot.period_id,
        "label": label,
        "total": {
            "mitigated": summary.get("mitigated"),
            "non_mitigated": summary.get("non_mitigated"),
            "exploitable": summary.get("exploitable"),
            "patch_available_over_30_days": summary.get(
                "patch_available_over_30_days"
            ),
        },
    }
    for severity in ACTIONABLE_SEVERITIES:
        result[severity] = {
            "mitigated": mitigated_by_severity.get(severity),
            "non_mitigated": open_by_severity.get(severity),
            "exploitable": exploitable_by_severity.get(severity),
            "patch_available_over_30_days": patch_by_severity.get(severity),
        }
    return result


def network_comparisons(
    predecessor: HistorySnapshot,
    current: HistorySnapshot,
    *,
    predecessor_label: str,
    current_label: str,
) -> list[dict[str, Any]]:
    previous_by_tag = {
        str(item.get("tag_uuid")): item
        for item in predecessor.network_tag_snapshots
        if item.get("tag_uuid")
    }
    comparisons: list[dict[str, Any]] = []
    for item in current.network_tag_snapshots:
        tag_uuid = str(item.get("tag_uuid") or "")
        previous = previous_by_tag.get(tag_uuid)
        if not tag_uuid or previous is None:
            continue
        comparisons.append({
            "tag_uuid": tag_uuid,
            "category": item.get("category"),
            "network": item.get("network") or item.get("label"),
            "periods": [
                {
                    "period_id": predecessor.period_id,
                    "label": predecessor_label,
                    "assets": list(previous.get("assets") or ()),
                },
                {
                    "period_id": current.period_id,
                    "label": current_label,
                    "assets": list(item.get("assets") or ()),
                },
            ],
        })
    return comparisons


def finding_transitions(
    predecessor: HistorySnapshot,
    current: HistorySnapshot,
) -> dict[str, Any]:
    previous_open = set(predecessor.open_finding_keys)
    current_open = set(current.open_finding_keys)
    current_fixed = set(current.fixed_finding_keys)
    current_resurfaced = set(current.resurfaced_finding_keys)
    return {
        "grain": "stable_finding_identity",
        "new": [value.hex() for value in sorted(
            current_open - previous_open - current_resurfaced
        )],
        "corrected": [value.hex() for value in sorted(previous_open & current_fixed)],
        "resurfaced": [value.hex() for value in sorted(current_resurfaced)],
        "persistent": [value.hex() for value in sorted(previous_open & current_open)],
    }


def vulnerability_evolution(
    predecessor: HistorySnapshot,
    current: HistorySnapshot,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    previous = {
        str(item.get("plugin_id")): int(item.get("count") or 0)
        for item in predecessor.open_plugin_counts
        if item.get("plugin_id") is not None
    }
    changes: list[dict[str, Any]] = []
    for item in current.open_plugin_counts:
        raw_plugin_id = item.get("plugin_id")
        if raw_plugin_id is None:
            continue
        plugin_key = str(raw_plugin_id)
        change = int(item.get("count") or 0) - previous.get(plugin_key, 0)
        if change <= 0:
            continue
        try:
            plugin_id: int | str = int(raw_plugin_id)
        except (TypeError, ValueError):
            plugin_id = plugin_key
        changes.append({
            "plugin_id": plugin_id,
            "label": str(item.get("plugin_name") or f"Plugin {plugin_key}"),
            "change": change,
        })
    changes.sort(key=lambda item: (-int(item["change"]), str(item["plugin_id"])))
    return changes[:max(0, int(limit))]
