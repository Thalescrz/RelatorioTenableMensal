from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tenable_reports.application.collect import (
    CollectionResult,
    VulnerabilityExportRequest,
    collect_vm_snapshot_by_state,
)
from tenable_reports.application.normalize import _collection_records
from tenable_reports.domain.normalization import normalize_findings
from tenable_reports.infrastructure.tenable_vm.client import ApiError


VM_REPORT_PROPERTIES = (
    "source",
    "indexed_at",
    "port",
    "protocol",
    "service",
    "severity",
    "first_observed",
    "last_fixed",
    "last_seen",
    "state",
    "resurfaced_date",
    "asset.name",
    "asset.fqdns",
    "asset.ipv4_addresses",
    "asset.ipv6_addresses",
    "asset.netbios_name",
    "asset.operating_systems",
    "definition.cve",
    "definition.description",
    "definition.family",
    "definition.name",
    "definition.see_also",
    "definition.solution",
    "definition.synopsis",
    "definition.references",
    "definition.workaround",
    "definition.workaround_type",
    "definition.has_workaround",
    "definition.vendor_unpatched",
    "definition.cvss2.base_score",
    "definition.cvss2.base_vector",
    "definition.cvss3.base_score",
    "definition.cvss3.base_vector",
    "definition.cvss4.base_score",
    "definition.cvss4.base_vector",
    "definition.vpr.score",
)

REQUIRED_SELECTIVE_PROPERTIES = (
    "id",
    "asset.id",
    "definition.id",
    "source",
    "port",
    "protocol",
    "severity",
    "first_observed",
    "last_seen",
    "state",
    "definition.description",
    "definition.family",
    "definition.name",
    "definition.solution",
    "definition.synopsis",
    "definition.cvss3.base_score",
    "definition.cvss3.base_vector",
    "definition.vpr.score",
)


@dataclass(frozen=True, slots=True)
class SelectiveContractResult:
    record_count: int
    missing_properties: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.missing_properties


@dataclass(frozen=True, slots=True)
class VmExportComparison:
    status: str
    differences: tuple[str, ...]
    full_metrics: Mapping[str, Any]
    selective_metrics: Mapping[str, Any]

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "differences": list(self.differences),
            "full": dict(self.full_metrics),
            "selective": dict(self.selective_metrics),
        }


@dataclass(frozen=True, slots=True)
class VmExportPolicyResult:
    collection: Any
    mode: str
    outcome: str
    comparison_path: Path | None = None
    fallback_reason: str | None = None


def recovery_vm_strategy(
    *,
    current_strategy: str,
    failure: BaseException,
    explicit_retry: bool,
) -> str:
    strategy = str(current_strategy).strip().lower()
    if strategy not in {"combined", "split"}:
        raise ValueError("current_strategy deve ser combined ou split.")
    if (
        explicit_retry
        and strategy == "combined"
        and getattr(failure, "timeout_phase", None) == "no_progress"
    ):
        return "split"
    return strategy

def selective_vm_properties(*, include_output: bool) -> tuple[str, ...]:
    if include_output:
        return (*VM_REPORT_PROPERTIES, "output")
    return VM_REPORT_PROPERTIES


def _has_path(record: Mapping[str, Any], dotted: str) -> bool:
    current: Any = record
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return True


def validate_selective_records(
    records: Iterable[Mapping[str, Any]],
) -> SelectiveContractResult:
    materialized = tuple(records)
    missing = {
        path
        for record in materialized
        for path in REQUIRED_SELECTIVE_PROPERTIES
        if not _has_path(record, path)
    }
    return SelectiveContractResult(
        record_count=len(materialized),
        missing_properties=tuple(sorted(missing)),
    )


def _normalized_findings(records: Sequence[Mapping[str, Any]]) -> tuple[Any, ...]:
    findings, _, _ = normalize_findings(
        records,
        client_id="vm-export-validation",
        assets_by_id={},
    )
    return findings


def _comparison_metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    findings = _normalized_findings(records)
    identity_hashes = sorted(
        hashlib.sha256(item.finding_key.encode("utf-8")).hexdigest()
        for item in findings
    )
    severity_counts = Counter(item.severity for item in findings)
    state_counts = Counter(item.state for item in findings)
    framework_counts: Counter[str] = Counter()
    plugin_assets: dict[int, set[str]] = defaultdict(set)
    plugin_scores: dict[int, tuple[float, int]] = {}
    severity_rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
    for item in findings:
        framework_counts.update(item.exploit_frameworks)
        plugin_assets[item.plugin_id].add(item.source_asset_id)
        score = (item.vpr_score or -1.0, severity_rank.get(item.severity, -1))
        plugin_scores[item.plugin_id] = max(plugin_scores.get(item.plugin_id, score), score)
    top_plugins = sorted(
        plugin_assets,
        key=lambda plugin_id: (
            -plugin_scores[plugin_id][0],
            -plugin_scores[plugin_id][1],
            -len(plugin_assets[plugin_id]),
            plugin_id,
        ),
    )[:5]
    return {
        "record_count": len(findings),
        "identity_digest": hashlib.sha256(
            "\n".join(identity_hashes).encode("utf-8")
        ).hexdigest(),
        "severity_counts": dict(sorted(severity_counts.items())),
        "state_counts": dict(sorted(state_counts.items())),
        "top5_plugin_ids": top_plugins,
        "temporal_counts": {
            "first_observed": sum(item.first_found_at is not None for item in findings),
            "fixed": sum(item.last_fixed_at is not None for item in findings),
            "resurfaced": sum(item.resurfaced_at is not None for item in findings),
        },
        "exploitable_count": sum(item.exploitable is True for item in findings),
        "framework_counts": dict(sorted(framework_counts.items())),
        "coverage": {
            "synopsis": sum(item.synopsis is not None for item in findings),
            "description": sum(item.description is not None for item in findings),
            "solution": sum(item.solution is not None for item in findings),
            "references": sum(bool(item.references) for item in findings),
            "cvss2": sum(item.cvss2_base_score is not None for item in findings),
            "cvss3": sum(item.cvss3_base_score is not None for item in findings),
            "vpr": sum(item.vpr_score is not None for item in findings),
        },
    }


def compare_vm_exports(
    full_records: Iterable[Mapping[str, Any]],
    selective_records: Iterable[Mapping[str, Any]],
) -> VmExportComparison:
    full_metrics = _comparison_metrics(tuple(full_records))
    selective_metrics = _comparison_metrics(tuple(selective_records))
    differences = tuple(
        key for key in full_metrics if full_metrics[key] != selective_metrics[key]
    )
    return VmExportComparison(
        status="PASSED" if not differences else "FAILED",
        differences=differences,
        full_metrics=full_metrics,
        selective_metrics=selective_metrics,
    )


def _records(collection: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(collection, CollectionResult):
        return tuple(_collection_records(collection))
    return tuple(dict(item) for item in getattr(collection, "records", ()) or ())


def _write_comparison(
    *,
    output_root: str | Path,
    client_id: str,
    run_id: str,
    comparison: VmExportComparison,
) -> Path:
    path = (
        Path(output_root) / "validation" / client_id / run_id
        / "vm-export-comparison.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(comparison.to_dict(), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return path


def collect_vm_snapshot_with_policy(
    *,
    client: Any,
    profile: Any,
    request: VulnerabilityExportRequest,
    output_root: str | Path,
    run_id: str,
    mode: str,
    strategy: str,
    collector: Callable[..., Any] = collect_vm_snapshot_by_state,
    **collection_kwargs: Any,
) -> VmExportPolicyResult:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"disabled", "validation", "enabled"}:
        raise ValueError("mode deve ser disabled, validation ou enabled.")

    primary_resume = collection_kwargs.pop("resume_from", None)

    def collect(
        export_request: VulnerabilityExportRequest,
        suffix: str | None,
        *,
        resume_from: str | Path | None = None,
    ) -> Any:
        kwargs = {
            "client": client,
            "profile": profile,
            "request": export_request,
            "output_root": output_root,
            "run_id": run_id,
            "strategy": strategy,
            **collection_kwargs,
        }
        if suffix is not None:
            kwargs["snapshot_suffix"] = suffix
        if resume_from is not None:
            kwargs["resume_from"] = resume_from
        return collector(**kwargs)

    if normalized_mode == "disabled":
        return VmExportPolicyResult(
            collection=collect(
                replace(request, properties=()),
                None,
                resume_from=primary_resume,
            ),
            mode=normalized_mode,
            outcome="FULL",
        )

    selective_request = replace(
        request,
        properties=selective_vm_properties(
            include_output=request.include_plugin_output
        ),
    )
    if normalized_mode == "enabled":
        try:
            selective = collect(
                selective_request,
                "selective",
                resume_from=primary_resume,
            )
        except ApiError as exc:
            if exc.status_code != 400:
                raise
            full = collect(replace(request, properties=()), "full")
            return VmExportPolicyResult(
                collection=full,
                mode=normalized_mode,
                outcome="FALLBACK_FULL",
                fallback_reason="HTTP_400",
            )
        contract = validate_selective_records(_records(selective))
        if contract.passed:
            return VmExportPolicyResult(
                collection=selective,
                mode=normalized_mode,
                outcome="SELECTIVE",
            )
        full = collect(replace(request, properties=()), "full")
        return VmExportPolicyResult(
            collection=full,
            mode=normalized_mode,
            outcome="FALLBACK_FULL",
            fallback_reason="CONTRACT_INVALID",
        )

    full = collect(
        replace(request, properties=()),
        "full",
        resume_from=primary_resume,
    )
    try:
        selective = collect(selective_request, "selective")
    except ApiError as exc:
        if exc.status_code != 400:
            raise
        comparison = VmExportComparison(
            status="FAILED",
            differences=("selective_http_400",),
            full_metrics=_comparison_metrics(_records(full)),
            selective_metrics=_comparison_metrics(()),
        )
        comparison_path = _write_comparison(
            output_root=output_root,
            client_id=profile.client_id,
            run_id=run_id,
            comparison=comparison,
        )
        return VmExportPolicyResult(
            collection=full,
            mode=normalized_mode,
            outcome="FAILED",
            comparison_path=comparison_path,
        )
    contract = validate_selective_records(_records(selective))
    comparison = compare_vm_exports(_records(full), _records(selective))
    if not contract.passed:
        differences = tuple(sorted({*comparison.differences, "selective_contract"}))
        comparison = VmExportComparison(
            status="FAILED",
            differences=differences,
            full_metrics=comparison.full_metrics,
            selective_metrics=comparison.selective_metrics,
        )
    comparison_path = _write_comparison(
        output_root=output_root,
        client_id=profile.client_id,
        run_id=run_id,
        comparison=comparison,
    )
    return VmExportPolicyResult(
        collection=full,
        mode=normalized_mode,
        outcome=comparison.status,
        comparison_path=comparison_path,
    )
