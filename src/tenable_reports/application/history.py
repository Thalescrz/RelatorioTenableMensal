from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo
from tenable_reports.infrastructure.jsonl_io import iter_jsonl_objects
from tenable_reports.domain.fingerprints import fingerprint_finding_key

from tenable_reports.config.profile import ClientProfile
from tenable_reports.application.report_registry import ReportRegistry
from tenable_reports.domain.history import (
    HISTORY_SCHEMA_VERSION,
    HistorySnapshot,
    SnapshotCompatibility,
    finding_transitions,
    monthly_history_row,
    network_comparisons,
    previous_period_overview,
    snapshots_compatible,
    summary_from_dataset,
    tag_snapshot_from_dataset,
    tag_year_history,
    vulnerability_evolution,
)
from tenable_reports.domain.report_reference import (
    READY_STATUS,
    ReportCandidate,
    ReportOrigin,
    ReportReferenceKey,
    expected_predecessor_key,
    reference_key_for_candidate,
)


@dataclass(frozen=True, slots=True)
class HistoryPublication:
    snapshot: HistorySnapshot
    predecessor: HistorySnapshot | None
    database_path: Path | None
    repository_backend: str
    repository_location: str
    enriched_dataset_path: Path
    csv_path: Path | None
    history_status: str


@dataclass(frozen=True, slots=True)
class HistoryPreparation:
    current: HistorySnapshot
    predecessor: HistorySnapshot | None
    reference_key: ReportReferenceKey
    candidate: ReportCandidate
    main_snapshots: tuple[HistorySnapshot, ...]
    database_path: Path | None
    repository_backend: str
    repository_location: str
    enriched_dataset_path: Path
    tag_enriched_dataset_paths: Mapping[str, Path]
    csv_path: Path | None
    history_status: str


class SnapshotRepository(ABC):
    @abstractmethod
    def publish(self, snapshot: HistorySnapshot) -> None:
        raise NotImplementedError

    @abstractmethod
    def compatible_snapshots(
        self,
        compatibility: SnapshotCompatibility,
        *,
        before_period_end_at: str,
    ) -> tuple[HistorySnapshot, ...]:
        raise NotImplementedError


class SQLiteSnapshotRepository(SnapshotRepository):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterable[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS history_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    execution_type TEXT NOT NULL,
                    period_mode TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    metric_definition_version TEXT NOT NULL,
                    scope_hash TEXT NOT NULL,
                    period_id TEXT NOT NULL,
                    period_start_at TEXT NOT NULL,
                    period_end_at TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_history_predecessor
                ON history_snapshots (
                    client_id, tenant_id, execution_type, period_mode, timezone,
                    metric_definition_version, scope_hash, period_end_at
                );
                """
            )

    def publish(self, snapshot: HistorySnapshot) -> None:
        payload = json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO history_snapshots (
                        snapshot_id, client_id, tenant_id, execution_type, period_mode,
                        timezone, metric_definition_version, scope_hash, period_id,
                        period_start_at, period_end_at, run_id, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.compatibility.client_id,
                        snapshot.compatibility.tenant_id,
                        snapshot.compatibility.execution_type,
                        snapshot.compatibility.period_mode,
                        snapshot.compatibility.timezone,
                        snapshot.compatibility.metric_definition_version,
                        snapshot.compatibility.scope_hash,
                        snapshot.period_id,
                        snapshot.period_start_at,
                        snapshot.period_end_at,
                        snapshot.run_id,
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = connection.execute(
                    "SELECT payload_json FROM history_snapshots WHERE snapshot_id = ?",
                    (snapshot.snapshot_id,),
                ).fetchone()
                if existing and str(existing[0]) == payload:
                    return
                raise ValueError(
                    "Ja existe snapshot historico diferente para esta competencia."
                ) from exc

    def compatible_snapshots(
        self,
        compatibility: SnapshotCompatibility,
        *,
        before_period_end_at: str,
    ) -> tuple[HistorySnapshot, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM history_snapshots
                WHERE client_id = ? AND tenant_id = ? AND execution_type = ?
                  AND period_mode = ? AND timezone = ?
                  AND metric_definition_version = ? AND scope_hash = ?
                  AND period_end_at <= ?
                ORDER BY period_end_at ASC, snapshot_id ASC
                """,
                (
                    compatibility.client_id,
                    compatibility.tenant_id,
                    compatibility.execution_type,
                    compatibility.period_mode,
                    compatibility.timezone,
                    compatibility.metric_definition_version,
                    compatibility.scope_hash,
                    before_period_end_at,
                ),
            ).fetchall()
        snapshots = tuple(HistorySnapshot.from_dict(json.loads(row[0])) for row in rows)
        if any(not snapshots_compatible(compatibility, item.compatibility) for item in snapshots):
            raise ValueError("Repositorio retornou snapshot historico incompativel.")
        return snapshots

    def all_snapshots(self) -> tuple[HistorySnapshot, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM history_snapshots "
                "ORDER BY period_end_at ASC, snapshot_id ASC"
            ).fetchall()
        return tuple(HistorySnapshot.from_dict(json.loads(row[0])) for row in rows)


def _read_dataset(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Nao foi possivel ler o dataset: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Dataset JSON invalido na linha {exc.lineno}.") from exc
    if not isinstance(data, dict):
        raise ValueError("O dataset deve conter um objeto JSON.")
    return data


def _normalized_keys(
    path: Path,
    *,
    period: Mapping[str, Any],
) -> tuple[tuple[bytes, ...], tuple[bytes, ...], tuple[bytes, ...], tuple[dict[str, Any], ...]]:
    start_at = datetime.fromisoformat(str(period["start_at"]).replace("Z", "+00:00"))
    end_at = datetime.fromisoformat(str(period["end_at"]).replace("Z", "+00:00"))
    open_keys: set[bytes] = set()
    fixed_keys: set[bytes] = set()
    resurfaced_keys: set[bytes] = set()
    plugin_counts: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        raise ValueError(f"Findings normalizados nao encontrados: {path}")
    for item in iter_jsonl_objects(path):
            finding_key = str(item.get("finding_key") or "")
            state = str(item.get("state") or "").upper()
            event_name = "last_fixed_at" if state == "FIXED" else "last_found_at"
            raw_event = item.get(event_name)
            try:
                event = datetime.fromisoformat(str(raw_event).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if not finding_key or not start_at <= event < end_at:
                continue
            fingerprint = fingerprint_finding_key(finding_key)
            if state in {"OPEN", "REOPENED"}:
                open_keys.add(fingerprint)
                raw_plugin_id = item.get("plugin_id")
                if raw_plugin_id is not None:
                    plugin_key = str(raw_plugin_id)
                    entry = plugin_counts.setdefault(plugin_key, {
                        "plugin_id": raw_plugin_id,
                        "plugin_name": str(item.get("plugin_name") or ""),
                        "count": 0,
                    })
                    entry["count"] = int(entry["count"]) + 1
                    if not entry.get("plugin_name") and item.get("plugin_name"):
                        entry["plugin_name"] = str(item["plugin_name"])
            elif state == "FIXED":
                fixed_keys.add(fingerprint)
            if state == "REOPENED":
                try:
                    resurfaced_at = datetime.fromisoformat(
                        str(item.get("resurfaced_at")).replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    resurfaced_at = None
                if resurfaced_at is not None and start_at <= resurfaced_at < end_at:
                    resurfaced_keys.add(fingerprint)
    return (
        tuple(sorted(open_keys)),
        tuple(sorted(fixed_keys)),
        tuple(sorted(resurfaced_keys)),
        tuple(
            plugin_counts[key]
            for key in sorted(plugin_counts, key=lambda value: (len(value), value))
        ),
    )


def _scope_hash(profile: ClientProfile, dataset: Mapping[str, Any]) -> str:
    coverage = dataset.get("source_coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    payload = {
        "vm_asset_groups": list(profile.vm_scope.asset_groups),
        "vm_include_unlicensed": profile.vm_scope.include_unlicensed,
        "was_enabled": profile.was_scope.enabled,
        "was_application_ids": list(profile.was_scope.application_ids),
        "include_info_severity": profile.reporting.include_info_severity,
        "requested_finding_states": coverage.get("requested_finding_states"),
        "open_metrics_collected": coverage.get("open_metrics_collected"),
        "fixed_metrics_collected": coverage.get("fixed_metrics_collected"),
        "general_collection_filtered_by_tags": coverage.get(
            "general_collection_filtered_by_tags"
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _period_label(snapshot: HistorySnapshot, *, short: bool = False) -> str:
    local = datetime.fromisoformat(snapshot.period_start_at.replace("Z", "+00:00")).astimezone(
        ZoneInfo(snapshot.compatibility.timezone)
    )
    months = (
        "", "Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
    )
    return f"{months[local.month]}/{str(local.year)[-2:]}" if short else (
        f"{months[local.month]}/{local.year}"
    )


def _history_snapshot(
    *,
    profile: ClientProfile,
    dataset: Mapping[str, Any],
    dataset_path: Path,
    normalized_findings_path: Path,
    tag_datasets: Iterable[Mapping[str, Any]] = (),
) -> HistorySnapshot:
    period = dataset.get("period")
    if not isinstance(period, Mapping):
        raise ValueError("Dataset sem periodo valido.")
    open_keys, fixed_keys, resurfaced_keys, open_plugin_counts = _normalized_keys(
        normalized_findings_path,
        period=period,
    )
    compatibility = SnapshotCompatibility(
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        execution_type=str(dataset.get("execution_type") or "UNSPECIFIED"),
        period_mode=str(period.get("mode") or ""),
        timezone=str(period.get("timezone") or profile.reporting.timezone),
        metric_definition_version=str(dataset.get("metric_definition_version") or ""),
        scope_hash=_scope_hash(profile, dataset),
    )
    customizations = dataset.get("customizations")
    customizations = customizations if isinstance(customizations, Mapping) else {}
    identity = {
        "compatibility": compatibility.to_dict(),
        "period_id": period.get("period_id"),
        "period_end_at": period.get("end_at"),
        "run_id": dataset.get("run_id"),
    }
    snapshot_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    legacy_tag_rows = tuple(
        dict(item)
        for item in customizations.get("network_tag_snapshots") or ()
        if isinstance(item, Mapping)
    )
    tag_rows_by_uuid = {
        str(item.get("tag_uuid") or ""): item
        for item in legacy_tag_rows
        if str(item.get("tag_uuid") or "")
    }
    for tag_dataset in tag_datasets:
        compact = tag_snapshot_from_dataset(tag_dataset)
        tag_rows_by_uuid[compact["tag_uuid"]] = compact
    return HistorySnapshot(
        snapshot_id=snapshot_id,
        run_id=str(dataset.get("run_id") or ""),
        period_id=str(period.get("period_id") or ""),
        period_start_at=str(period.get("start_at") or ""),
        period_end_at=str(period.get("end_at") or ""),
        generated_at=str(dataset.get("generated_at") or ""),
        compatibility=compatibility,
        summary=summary_from_dataset(dataset),
        open_finding_keys=open_keys,
        fixed_finding_keys=fixed_keys,
        resurfaced_finding_keys=resurfaced_keys,
        tag_snapshots=tuple(tag_rows_by_uuid[key] for key in sorted(tag_rows_by_uuid)),
        open_plugin_counts=open_plugin_counts,
        source_dataset_path=str(dataset_path.resolve()),
        source_dataset_sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
    )


def _merge_customizations(
    dataset: Mapping[str, Any],
    *,
    snapshots: Iterable[HistorySnapshot],
    current: HistorySnapshot,
    predecessor: HistorySnapshot | None,
    missing_status: str = "NO_COMPATIBLE_PREDECESSOR",
) -> dict[str, Any]:
    result = dict(dataset)
    existing = result.get("customizations")
    customizations = dict(existing) if isinstance(existing, Mapping) else {}
    all_snapshots = [*snapshots, current]
    monthly_history = [
        monthly_history_row(item, label=_period_label(item)) for item in all_snapshots
    ]
    customizations["monthly_history"] = monthly_history
    customizations["monthly_views"] = [{
        "id": "general",
        "label": "Geral",
        "history": monthly_history,
    }]
    if predecessor is not None:
        customizations["previous_period_overview"] = previous_period_overview(
            predecessor,
            label=_period_label(predecessor).replace("/", " "),
        )
        customizations["finding_transitions"] = finding_transitions(
            predecessor, current
        )
        evolution = vulnerability_evolution(predecessor, current)
        customizations["vulnerability_evolution"] = evolution
        customizations["vulnerability_evolution_status"] = (
            "AVAILABLE" if evolution else "NO_OCCURRENCES"
        )
        comparisons = network_comparisons(
            predecessor,
            current,
            predecessor_label=_period_label(predecessor, short=True),
            current_label=_period_label(current, short=True),
        )
        if comparisons:
            customizations["network_comparisons"] = comparisons
        else:
            customizations.pop("network_comparisons", None)
        customizations["history_status"] = {
            "status": "COMPATIBLE_PREDECESSOR",
            "predecessor_period_id": predecessor.period_id,
            "predecessor_snapshot_id": predecessor.snapshot_id,
        }
    else:
        for key in (
            "previous_period_overview",
            "finding_transitions",
            "network_comparisons",
        ):
            customizations.pop(key, None)
        customizations["vulnerability_evolution"] = []
        customizations["vulnerability_evolution_status"] = "NO_HISTORY"
        customizations["history_status"] = {
            "status": missing_status,
            "message": "Sem historico comparavel para esta competencia.",
        }
    result["customizations"] = customizations
    return result


def _export_csv(path: Path, snapshots: Iterable[HistorySnapshot]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "snapshot_id", "client_id", "tenant_id", "execution_type", "period_mode",
        "timezone", "period_id", "period_start_at", "period_end_at", "run_id",
        "generated_at", "metric_definition_version", "scope_hash", "summary_json",
        "tag_snapshots_json",
    )
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        rows.append({
            "snapshot_id": snapshot.snapshot_id,
            "client_id": snapshot.compatibility.client_id,
            "tenant_id": snapshot.compatibility.tenant_id,
            "execution_type": snapshot.compatibility.execution_type,
            "period_mode": snapshot.compatibility.period_mode,
            "timezone": snapshot.compatibility.timezone,
            "period_id": snapshot.period_id,
            "period_start_at": snapshot.period_start_at,
            "period_end_at": snapshot.period_end_at,
            "run_id": snapshot.run_id,
            "generated_at": snapshot.generated_at,
            "metric_definition_version": snapshot.compatibility.metric_definition_version,
            "scope_hash": snapshot.compatibility.scope_hash,
            "summary_json": json.dumps(snapshot.summary, ensure_ascii=False, sort_keys=True),
            "tag_snapshots_json": json.dumps(
                list(snapshot.tag_snapshots), ensure_ascii=False, sort_keys=True
            ),
        })
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    content = "\ufeff" + buffer.getvalue()
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return
        raise FileExistsError(f"Export historico ja existe com outro conteudo: {path}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(content)


def import_history_csv(
    *,
    csv_path: str | Path,
    database_path: str | Path | None = None,
    repository: SnapshotRepository | None = None,
) -> tuple[HistorySnapshot, ...]:
    source = Path(csv_path)
    if repository is None and database_path is None:
        raise ValueError("database_path ou repository deve ser informado.")
    store = repository or SQLiteSnapshotRepository(database_path or "")
    snapshots: list[HistorySnapshot] = []
    try:
        stream = source.open(encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"Nao foi possivel ler o CSV historico: {source}") from exc
    with stream:
        for line_number, row in enumerate(csv.DictReader(stream), start=2):
            try:
                summary = json.loads(str(row.get("summary_json") or ""))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"summary_json invalido no CSV historico, linha {line_number}."
                ) from exc
            if not isinstance(summary, Mapping):
                raise ValueError(
                    f"summary_json precisa ser objeto na linha {line_number}."
                )
            raw_tag_snapshots = str(row.get("tag_snapshots_json") or "").strip()
            if raw_tag_snapshots:
                try:
                    tag_snapshots = json.loads(raw_tag_snapshots)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"tag_snapshots_json invalido no CSV historico, linha {line_number}."
                    ) from exc
                if not isinstance(tag_snapshots, list) or any(
                    not isinstance(item, Mapping) for item in tag_snapshots
                ):
                    raise ValueError(
                        f"tag_snapshots_json precisa ser lista na linha {line_number}."
                    )
            else:
                tag_snapshots = []
            compatibility = SnapshotCompatibility(
                client_id=str(row.get("client_id") or ""),
                tenant_id=str(row.get("tenant_id") or ""),
                execution_type=str(row.get("execution_type") or ""),
                period_mode=str(row.get("period_mode") or ""),
                timezone=str(row.get("timezone") or ""),
                metric_definition_version=str(
                    row.get("metric_definition_version") or ""
                ),
                scope_hash=str(row.get("scope_hash") or ""),
            )
            required = (
                row.get("snapshot_id"), row.get("period_id"),
                row.get("period_start_at"), row.get("period_end_at"),
                compatibility.client_id, compatibility.tenant_id,
                compatibility.execution_type, compatibility.period_mode,
                compatibility.timezone, compatibility.metric_definition_version,
                compatibility.scope_hash,
            )
            if any(not str(value or "").strip() for value in required):
                raise ValueError(
                    f"CSV historico possui campo obrigatorio vazio na linha {line_number}."
                )
            snapshot = HistorySnapshot(
                snapshot_id=str(row["snapshot_id"]),
                run_id=str(row.get("run_id") or "csv-import"),
                period_id=str(row["period_id"]),
                period_start_at=str(row["period_start_at"]),
                period_end_at=str(row["period_end_at"]),
                generated_at=str(row.get("generated_at") or ""),
                compatibility=compatibility,
                summary=dict(summary),
                open_finding_keys=(),
                fixed_finding_keys=(),
                resurfaced_finding_keys=(),
                tag_snapshots=tuple(dict(item) for item in tag_snapshots),
                source_dataset_path=f"csv:{source.resolve()}",
            )
            store.publish(snapshot)
            snapshots.append(snapshot)
    return tuple(snapshots)


def _repository_metadata(
    repository: SnapshotRepository | None,
    database_path: str | Path | None,
) -> tuple[str, str, Path | None]:
    if isinstance(repository, SQLiteSnapshotRepository):
        return "sqlite", str(repository.path.resolve()), repository.path
    if repository is not None:
        return "postgresql", str(getattr(repository, "location", "postgresql")), None
    if database_path is not None:
        path = Path(database_path)
        return "sqlite", str(path.resolve()), path
    return "unconfigured", "unconfigured", None


def _candidate_for_snapshot(
    snapshot: HistorySnapshot,
    dataset: Mapping[str, Any],
    *,
    origin_override: str | None = None,
) -> ReportCandidate:
    raw_origin = str(origin_override or dataset.get("origin") or "").strip().upper()
    if raw_origin:
        try:
            origin = ReportOrigin(raw_origin)
        except ValueError:
            origin = ReportOrigin.MANUAL
    elif snapshot.compatibility.execution_type == "AUTOMATIC_MONTHLY":
        origin = ReportOrigin.SCHEDULED
    else:
        origin = ReportOrigin.MANUAL
    return ReportCandidate(
        run_id=snapshot.run_id,
        client_id=snapshot.compatibility.client_id,
        tenant_id=snapshot.compatibility.tenant_id,
        origin=origin,
        execution_type=snapshot.compatibility.execution_type,
        period_start_at=snapshot.period_start_at,
        period_end_at=snapshot.period_end_at,
        period_mode=snapshot.compatibility.period_mode,
        timezone=snapshot.compatibility.timezone,
        scope_hash=snapshot.compatibility.scope_hash,
        metric_definition_version=snapshot.compatibility.metric_definition_version,
        publication_status=READY_STATUS,
        documents_valid=True,
    )


def _write_enriched_dataset(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(
                f"Dataset enriquecido ja existe com outro conteudo: {path}"
            )
        return
    path.write_text(content, encoding="utf-8")


def _read_tag_datasets(
    paths: Mapping[str, str | Path] | None,
    *,
    profile: ClientProfile,
    run_id: str,
) -> dict[str, tuple[Path, dict[str, Any]]]:
    datasets: dict[str, tuple[Path, dict[str, Any]]] = {}
    for expected_uuid, raw_path in (paths or {}).items():
        path = Path(raw_path)
        data = _read_dataset(path)
        tag = data.get("tag")
        if not isinstance(tag, Mapping):
            raise ValueError(f"Dataset por TAG sem identificacao valida: {path}")
        tag_uuid = str(tag.get("tag_uuid") or "").strip()
        if tag_uuid != str(expected_uuid):
            raise ValueError("O UUID informado nao corresponde ao dataset por TAG.")
        if data.get("client_id") != profile.client_id:
            raise ValueError("O dataset por TAG nao pertence ao cliente selecionado.")
        if str(data.get("run_id") or "") != run_id:
            raise ValueError("O dataset por TAG nao pertence ao run_id selecionado.")
        datasets[tag_uuid] = (path, data)
    return datasets


def _enrich_tag_datasets(
    datasets: Mapping[str, tuple[Path, Mapping[str, Any]]],
    *,
    snapshots: Iterable[HistorySnapshot],
    current: HistorySnapshot,
) -> dict[str, Path]:
    outputs: dict[str, Path] = {}
    compatible_monthly = (
        current.compatibility.period_mode == "PREVIOUS_CALENDAR_MONTH"
        and len(current.period_id) == 7
        and current.period_id[4:5] == "-"
    )
    for tag_uuid, (source_path, data) in datasets.items():
        enriched = dict(data)
        tag = data.get("tag")
        comparison_enabled = bool(
            tag.get("include_temporal_comparison", False)
            if isinstance(tag, Mapping)
            else False
        )
        if not comparison_enabled:
            enriched["tag_history_status"] = "DISABLED"
            enriched["tag_history"] = []
        elif not compatible_monthly:
            enriched["tag_history_status"] = "INCOMPATIBLE_PERIOD"
            enriched["tag_history"] = []
        else:
            enriched["tag_history_status"] = "AVAILABLE"
            history = list(
                tag_year_history(snapshots, current=current, tag_uuid=tag_uuid)
            )
            enriched["tag_history"] = history
            try:
                current_year, current_month = (
                    int(value) for value in current.period_id.split("-", 1)
                )
            except (TypeError, ValueError):
                previous_period_id = ""
            else:
                previous_year = current_year if current_month > 1 else current_year - 1
                previous_month = current_month - 1 if current_month > 1 else 12
                previous_period_id = f"{previous_year:04d}-{previous_month:02d}"
            history_by_period = {
                str(row.get("period_id") or ""): row for row in history
            }
            previous_row = history_by_period.get(previous_period_id)
            current_row = history_by_period.get(current.period_id)
            if (
                previous_row is not None
                and current_row is not None
                and previous_row.get("availability") == "AVAILABLE"
                and current_row.get("availability") == "AVAILABLE"
                and isinstance(previous_row.get("top_assets"), list)
                and isinstance(current_row.get("top_assets"), list)
            ):
                enriched["tag_comparison"] = {
                    "periods": [
                        {
                            "period_id": previous_row["period_id"],
                            "label": previous_row["label"],
                            "top_assets": previous_row["top_assets"],
                        },
                        {
                            "period_id": current_row["period_id"],
                            "label": current_row["label"],
                            "top_assets": current_row["top_assets"],
                        },
                    ]
                }
            else:
                enriched.pop("tag_comparison", None)
        output = source_path.parent / "report-dataset-with-history.json"
        _write_enriched_dataset(output, enriched)
        outputs[tag_uuid] = output
    return outputs


def prepare_dataset_history(
    *,
    profile: ClientProfile,
    dataset_path: str | Path,
    normalized_findings_path: str | Path,
    output_path: str | Path,
    tag_dataset_paths: Mapping[str, str | Path] | None = None,
    registry: ReportRegistry,
    repository: SnapshotRepository | None = None,
    database_path: str | Path | None = None,
    csv_path: str | Path | None = None,
    origin: str | None = None,
) -> HistoryPreparation:
    dataset_file = Path(dataset_path)
    data = _read_dataset(dataset_file)
    if data.get("client_id") != profile.client_id:
        raise ValueError("O dataset nao pertence ao cliente selecionado.")
    run_id = str(data.get("run_id") or "")
    tag_datasets = _read_tag_datasets(
        tag_dataset_paths,
        profile=profile,
        run_id=run_id,
    )
    current = _history_snapshot(
        profile=profile,
        dataset=data,
        dataset_path=dataset_file,
        normalized_findings_path=Path(normalized_findings_path),
        tag_datasets=(value for _, value in tag_datasets.values()),
    )
    candidate = _candidate_for_snapshot(current, data, origin_override=origin)
    reference_key = reference_key_for_candidate(candidate)
    predecessor_key = expected_predecessor_key(reference_key)
    predecessor = (
        registry.get_main_snapshot(predecessor_key)
        if predecessor_key is not None
        else None
    )
    main_snapshots = registry.list_main_snapshots_before(reference_key)
    history_status = (
        "COMPATIBLE_PREDECESSOR" if predecessor is not None else "NO_IMMEDIATE_MAIN"
    )
    enriched = _merge_customizations(
        data,
        snapshots=main_snapshots,
        current=current,
        predecessor=predecessor,
        missing_status=history_status,
    )
    output = Path(output_path)
    _write_enriched_dataset(output, enriched)
    tag_enriched_paths = _enrich_tag_datasets(
        tag_datasets,
        snapshots=main_snapshots,
        current=current,
    )
    backend, location, local_database = _repository_metadata(repository, database_path)
    return HistoryPreparation(
        current=current,
        predecessor=predecessor,
        reference_key=reference_key,
        candidate=candidate,
        main_snapshots=main_snapshots,
        database_path=local_database,
        repository_backend=backend,
        repository_location=location,
        enriched_dataset_path=output,
        tag_enriched_dataset_paths=tag_enriched_paths,
        csv_path=Path(csv_path) if csv_path else None,
        history_status=history_status,
    )


def finalize_history_publication(
    preparation: HistoryPreparation,
    *,
    snapshot_repository: SnapshotRepository,
    registry: ReportRegistry,
    publication_validated: bool,
    auto_promote: bool = True,
) -> HistoryPublication:
    if not publication_validated:
        raise ValueError("Publicacao invalida nao pode entrar no historico.")
    registry.register_report(preparation.candidate)
    snapshot_repository.publish(preparation.current)
    registry.register_report(preparation.candidate, preparation.current)
    if auto_promote:
        registry.auto_promote_if_empty(
            preparation.reference_key,
            preparation.current.run_id,
        )
    if preparation.csv_path:
        _export_csv(
            preparation.csv_path,
            (*preparation.main_snapshots, preparation.current),
        )
    return HistoryPublication(
        snapshot=preparation.current,
        predecessor=preparation.predecessor,
        database_path=preparation.database_path,
        repository_backend=preparation.repository_backend,
        repository_location=preparation.repository_location,
        enriched_dataset_path=preparation.enriched_dataset_path,
        csv_path=preparation.csv_path,
        history_status=preparation.history_status,
    )


def publish_dataset_history(
    *,
    profile: ClientProfile,
    dataset_path: str | Path,
    normalized_findings_path: str | Path,
    database_path: str | Path | None = None,
    output_path: str | Path,
    csv_path: str | Path | None = None,
    repository: SnapshotRepository | None = None,
) -> HistoryPublication:
    dataset_file = Path(dataset_path)
    data = _read_dataset(dataset_file)
    if data.get("client_id") != profile.client_id:
        raise ValueError("O dataset nao pertence ao cliente selecionado.")
    current = _history_snapshot(
        profile=profile,
        dataset=data,
        dataset_path=dataset_file,
        normalized_findings_path=Path(normalized_findings_path),
    )
    if repository is None and database_path is None:
        raise ValueError("database_path ou repository deve ser informado.")
    store = repository or SQLiteSnapshotRepository(database_path or "")
    candidates = store.compatible_snapshots(
        current.compatibility,
        before_period_end_at=current.period_start_at,
    )
    predecessor = candidates[-1] if candidates else None
    enriched = _merge_customizations(
        data,
        snapshots=candidates,
        current=current,
        predecessor=predecessor,
    )
    output = Path(output_path)
    _write_enriched_dataset(output, enriched)
    store.publish(current)
    export = Path(csv_path) if csv_path else None
    if export:
        _export_csv(export, (*candidates, current))
    if isinstance(store, SQLiteSnapshotRepository):
        backend = "sqlite"
        location = str(store.path.resolve())
        local_path: Path | None = store.path
    else:
        backend = "postgresql"
        location = str(getattr(store, "location", "postgresql"))
        local_path = None
    return HistoryPublication(
        snapshot=current,
        predecessor=predecessor,
        database_path=local_path,
        repository_backend=backend,
        repository_location=location,
        enriched_dataset_path=output,
        csv_path=export,
        history_status=(
            "COMPATIBLE_PREDECESSOR" if predecessor else "NO_COMPATIBLE_PREDECESSOR"
        ),
    )
