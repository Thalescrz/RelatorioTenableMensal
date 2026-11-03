from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from tenable_reports.application.publishing import write_json_atomic
from tenable_reports.application.was_recovery import WasFailureDetails
from tenable_reports.config.profile import ClientProfile, Owasp2025Mode
from tenable_reports.domain.reporting import ReportingPeriod
from tenable_reports.infrastructure.tenable_vm.client import ApiError
from tenable_reports.infrastructure.tenable_was.owasp_analysis import (
    Owasp2025PluginGroup,
    TenableWasOwaspAnalysisClient,
)


CATEGORIES = tuple(f"A{index:02d}" for index in range(1, 11))
SOURCE = "console_analysis_exclusive"
SNAPSHOT_NAME = "tenable_was_owasp_2025.snapshot.json"


@dataclass(frozen=True, slots=True)
class Owasp2025CollectionAttempt:
    status: str
    snapshot_path: Path | None
    completed_categories: tuple[str, ...]
    warnings: tuple[Mapping[str, Any], ...]
    failure: WasFailureDetails | None


def _contract(profile: ClientProfile, period: ReportingPeriod) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": Owasp2025Mode.CONSOLE_EXCLUSIVE.value,
        "source": SOURCE,
        "period": {
            "start_at": period.to_dict()["start_at"],
            "end_at": period.to_dict()["end_at"],
            "timezone": period.timezone,
            "interval": "[start_at, end_at)",
        },
        "filters": {
            "state": {"operator": "neq", "value": ["FIXED"]},
            "severity": {"operator": "neq", "value": ["INFO"]},
            "risk_modified": {"operator": "neq", "value": ["ACCEPTED"]},
            "owasp_2025": {"operator": "eq", "value": "category"},
            "last_seen": {"operator": "between", "interval": "[start, end)"},
        },
        "group_by": ["definition.id"],
        "statistics": [
            "max:severity",
            "first:definition.name",
            "first:definition.family",
            "first:definition.vpr.score",
            "count:finding_id",
        ],
    }


def _canonical_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_group(group: Owasp2025PluginGroup) -> dict[str, Any]:
    return asdict(group)


def _deserialize_group(value: Any, *, category: str) -> Owasp2025PluginGroup:
    if not isinstance(value, Mapping):
        raise ValueError("Grupo OWASP persistido possui formato invalido.")
    persisted_category = str(value.get("category") or "")
    if persisted_category != category:
        raise ValueError("Grupo OWASP persistido pertence a outra categoria.")
    raw_vpr = value.get("vpr_score")
    return Owasp2025PluginGroup(
        category=category,
        plugin_id=int(value["plugin_id"]),
        name=str(value["name"]).strip() if value.get("name") is not None else None,
        family=(
            str(value["family"]).strip() if value.get("family") is not None else None
        ),
        severity=str(value["severity"]).strip().upper(),
        vpr_score=float(raw_vpr) if raw_vpr is not None else None,
        instances=int(value["instances"]),
    )


def _load_checkpoint(
    path: Path,
    *,
    profile: ClientProfile,
    period: ReportingPeriod,
    run_id: str,
    category: str,
    contract_sha256: str,
) -> tuple[Owasp2025PluginGroup, ...] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return None
        expected = {
            "schema_version": 1,
            "client_id": profile.client_id,
            "tenant_id": profile.tenant_id,
            "run_id": run_id,
            "category": category,
            "contract_sha256": contract_sha256,
            "period": period.to_dict(),
            "status": "COMPLETE",
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            return None
        groups = payload.get("groups")
        if not isinstance(groups, list):
            return None
        return tuple(_deserialize_group(item, category=category) for item in groups)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _load_complete_snapshot(
    path: Path,
    *,
    profile: ClientProfile,
    run_id: str,
    contract_sha256: str,
) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(payload, Mapping)
        and payload.get("client_id") == profile.client_id
        and payload.get("tenant_id") == profile.tenant_id
        and payload.get("run_id") == run_id
        and payload.get("contract_sha256") == contract_sha256
        and payload.get("coverage") == "COMPLETE"
        and set(payload.get("categories") or {}) == set(CATEGORIES)
    )


def collect_exclusive_owasp_2025_snapshot(
    *,
    client: TenableWasOwaspAnalysisClient,
    profile: ClientProfile,
    period: ReportingPeriod,
    output_root: str | Path,
    run_id: str,
    resume_run_id: str | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> Owasp2025CollectionAttempt:
    if profile.was_scope.owasp_2025_mode is not Owasp2025Mode.CONSOLE_EXCLUSIVE:
        raise ValueError("A analise exclusiva requer o modo console_exclusive.")
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("run_id e obrigatorio para a analise OWASP 2025.")
    normalized_resume_run_id = str(resume_run_id or "").strip() or None

    root = Path(output_root)
    checkpoint_root = (
        root / "recovery" / profile.client_id / normalized_run_id / "was-owasp-2025"
    )
    snapshot_path = (
        root / "snapshots" / profile.client_id / normalized_run_id / SNAPSHOT_NAME
    )
    contract = _contract(profile, period)
    contract_sha256 = _canonical_hash(contract)
    if _load_complete_snapshot(
        snapshot_path,
        profile=profile,
        run_id=normalized_run_id,
        contract_sha256=contract_sha256,
    ):
        return Owasp2025CollectionAttempt(
            status="COMPLETE",
            snapshot_path=snapshot_path,
            completed_categories=CATEGORIES,
            warnings=(),
            failure=None,
        )

    categories: dict[str, tuple[Owasp2025PluginGroup, ...]] = {}
    for category in CATEGORIES:
        checkpoint_path = checkpoint_root / f"{category}.json"
        groups = _load_checkpoint(
            checkpoint_path,
            profile=profile,
            period=period,
            run_id=normalized_run_id,
            category=category,
            contract_sha256=contract_sha256,
        )
        if (
            groups is None
            and normalized_resume_run_id is not None
            and normalized_resume_run_id != normalized_run_id
        ):
            source_checkpoint_path = (
                root
                / "recovery"
                / profile.client_id
                / normalized_resume_run_id
                / "was-owasp-2025"
                / f"{category}.json"
            )
            groups = _load_checkpoint(
                source_checkpoint_path,
                profile=profile,
                period=period,
                run_id=normalized_resume_run_id,
                category=category,
                contract_sha256=contract_sha256,
            )
            if groups is not None:
                write_json_atomic(checkpoint_path, {
                    "schema_version": 1,
                    "client_id": profile.client_id,
                    "tenant_id": profile.tenant_id,
                    "run_id": normalized_run_id,
                    "category": category,
                    "contract_sha256": contract_sha256,
                    "period": period.to_dict(),
                    "status": "COMPLETE",
                    "groups": [_serialize_group(item) for item in groups],
                })
        if groups is None:
            try:
                groups = client.list_category_plugins(
                    category=category,
                    start_at=period.start_at,
                    end_at=period.end_at,
                )
            except (ApiError, TimeoutError, OSError) as exc:
                message = (
                    "A analise exclusiva OWASP 2025 nao foi concluida. "
                    "O componente WAS pode ser tentado novamente."
                )
                failure = WasFailureDetails(
                    code="WAS_OWASP_2025_EXCLUSIVE_UNAVAILABLE",
                    message=message,
                    retryable=True,
                    failed_category=category,
                )
                return Owasp2025CollectionAttempt(
                    status="UNAVAILABLE",
                    snapshot_path=None,
                    completed_categories=tuple(categories),
                    warnings=({
                        "code": failure.code,
                        "message": message,
                        "retryable": True,
                        "category": category,
                    },),
                    failure=failure,
                )
            write_json_atomic(checkpoint_path, {
                "schema_version": 1,
                "client_id": profile.client_id,
                "tenant_id": profile.tenant_id,
                "run_id": normalized_run_id,
                "category": category,
                "contract_sha256": contract_sha256,
                "period": period.to_dict(),
                "status": "COMPLETE",
                "groups": [_serialize_group(item) for item in groups],
            })
        categories[category] = groups
        if progress_callback is not None:
            progress_callback({
                "component": "WAS",
                "stage": "OWASP_2025_EXCLUSIVE",
                "category": category,
                "completed_categories": len(categories),
                "total_categories": len(CATEGORIES),
            })

    snapshot_payload = {
        "schema_version": 1,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "run_id": normalized_run_id,
        "mode": Owasp2025Mode.CONSOLE_EXCLUSIVE.value,
        "source": SOURCE,
        "coverage": "COMPLETE",
        "period": period.to_dict(),
        "contract_sha256": contract_sha256,
        "category_counts": {
            category: len(categories[category]) for category in CATEGORIES
        },
        "categories": {
            category: [_serialize_group(item) for item in categories[category]]
            for category in CATEGORIES
        },
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    write_json_atomic(snapshot_path, snapshot_payload)
    return Owasp2025CollectionAttempt(
        status="COMPLETE",
        snapshot_path=snapshot_path,
        completed_categories=CATEGORIES,
        warnings=(),
        failure=None,
    )
