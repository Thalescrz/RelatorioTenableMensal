from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from tenable_reports.application.collection_execution import (  # noqa: E402
    materialize_compact_snapshot_run,
)
from tenable_reports.application.cloud_execution import (  # noqa: E402
    CloudExecutionRequest,
    _history_row,
    _with_monthly_history,
)
from tenable_reports.application.cloud_report_dataset import (  # noqa: E402
    load_cloud_report_dataset,
)
from tenable_reports.application.cloud_snapshots import (  # noqa: E402
    replay_cloud_snapshot,
)
from tenable_reports.application.compact_snapshots import (  # noqa: E402
    CompactFindingSnapshot,
    replay_compact_snapshot,
)
from tenable_reports.application.compact_publication import (  # noqa: E402
    prepare_compact_run_snapshot,
)
from tenable_reports.application.history import (  # noqa: E402
    HistoryComparisonOverride,
    build_history_snapshot,
    prepare_dataset_history,
)
from tenable_reports.application.publishing import (  # noqa: E402
    PublicationDocument,
    PublicationDocumentReplacement,
    refresh_publication_documents_atomically,
    sha256_file,
    write_json_atomic,
)
from tenable_reports.application.report_dataset import (  # noqa: E402
    build_report_dataset_from_snapshot,
)
from tenable_reports.application.tag_report_dataset import (  # noqa: E402
    build_tag_report_datasets_from_snapshot,
)
from tenable_reports.config.database import DatabaseConfig  # noqa: E402
from tenable_reports.config.environment import load_dotenv_file  # noqa: E402
from tenable_reports.config.profile import (  # noqa: E402
    ClientProfile,
    load_operational_client_profile,
)
from tenable_reports.domain.history import HistorySnapshot  # noqa: E402
from tenable_reports.domain.reporting import (  # noqa: E402
    PeriodMode,
    ReportingPeriod,
    parse_datetime,
)
from tenable_reports.infrastructure.compact_snapshots_postgresql import (  # noqa: E402
    PostgresCompactSnapshotRepository,
)
from tenable_reports.infrastructure.cloud_snapshots_postgresql import (  # noqa: E402
    PostgresCloudSnapshotRepository,
)
from tenable_reports.infrastructure.postgresql import (  # noqa: E402
    SCHEMA_NAME,
    PostgresDatabase,
    PostgresOperationsRepository,
    PostgresSnapshotRepository,
)
from tenable_reports.infrastructure.report_registry_postgresql import (  # noqa: E402
    PostgresReportRegistry,
)
from tenable_reports.infrastructure.translation import (  # noqa: E402
    build_default_text_translator,
)
from tenable_reports.presentation.cloud_report_docx import (  # noqa: E402
    generate_cloud_report,
)
from tenable_reports.presentation.customizations_report_docx import (  # noqa: E402
    generate_customizations_report,
)
from tenable_reports.presentation.tag_report_docx import (  # noqa: E402
    refresh_tag_temporal_comparison,
)


OPERATION = "MONTHLY_COMPARISON_REPAIR_V1"
ACTIVE_JOB_STATUSES = (
    "QUEUED",
    "RUNNING",
    "WAITING_WAS_DECISION",
    "INTERRUPT_REQUESTED",
)
CONTROLLED_SCOPE_REASON = "USER_AUTHORIZED_SCOPE_CHANGE"
CONTROLLED_SCOPE_NOTICE = (
    "Comparação autorizada entre competências com alteração controlada da cobertura "
    "de ativos sem licença. Os deltas podem refletir tanto a evolução mensal quanto "
    "essa mudança de escopo."
)


@dataclass(frozen=True, slots=True)
class MainRun:
    run_id: str
    client_id: str
    tenant_id: str
    period_key: str
    timezone: str
    scope_hash: str
    metric_definition_version: str
    execution_type: str
    period_start_at: str
    period_end_at: str
    period_mode: str
    origin: str
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class RepairPlanItem:
    current: MainRun
    predecessor: MainRun
    profile_path: Path
    profile: ClientProfile
    compact_snapshot: CompactFindingSnapshot
    predecessor_compact_snapshot: CompactFindingSnapshot | None
    current_snapshot: HistorySnapshot
    predecessor_snapshot: HistorySnapshot
    documents: tuple[PublicationDocument, ...]
    controlled_scope_override: bool


def _overlay_tag_top_assets(
    snapshot: HistorySnapshot,
    recovered_rows: Sequence[Mapping[str, Any]],
) -> HistorySnapshot:
    recovered_by_uuid = {
        str(item.get("tag_uuid") or ""): item
        for item in recovered_rows
        if str(item.get("tag_uuid") or "")
    }
    changed = False
    tag_snapshots: list[dict[str, Any]] = []
    for stored in snapshot.tag_snapshots:
        row = dict(stored)
        recovered = recovered_by_uuid.get(str(row.get("tag_uuid") or ""))
        if recovered is not None:
            recovered_assets = recovered.get("top_assets")
            if recovered_assets is None:
                recovered_assets = recovered.get("assets")
            stored_assets = row.get("top_assets")
            if stored_assets is None:
                stored_assets = row.get("assets")
            if (
                isinstance(recovered_assets, (list, tuple))
                and len(recovered_assets) > len(stored_assets or ())
            ):
                row["top_assets"] = [
                    dict(item)
                    for item in recovered_assets[:20]
                    if isinstance(item, Mapping)
                ]
                changed = True
        tag_snapshots.append(row)
    if not changed:
        return snapshot
    return replace(snapshot, tag_snapshots=tuple(tag_snapshots))


class _SnapshotOverlayRegistry:
    def __init__(self, base: Any, replacement: HistorySnapshot) -> None:
        self._base = base
        self._replacement = replacement

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    def get_main_snapshot(self, key: Any) -> HistorySnapshot | None:
        snapshot = self._base.get_main_snapshot(key)
        if snapshot is not None and snapshot.run_id == self._replacement.run_id:
            return self._replacement
        return snapshot

    def list_main_snapshots_before(self, key: Any) -> tuple[HistorySnapshot, ...]:
        return tuple(
            self._replacement if item.run_id == self._replacement.run_id else item
            for item in self._base.list_main_snapshots_before(key)
        )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def _previous_month(period_id: str) -> str:
    try:
        value = datetime.strptime(period_id, "%Y-%m")
    except ValueError as exc:
        raise ValueError("A competência precisa usar o formato YYYY-MM.") from exc
    if value.month == 1:
        return f"{value.year - 1:04d}-12"
    return f"{value.year:04d}-{value.month - 1:02d}"


def _cloud_dataset_matches_period(
    period: Mapping[str, Any],
    *,
    period_key: str,
    timezone: str,
) -> bool:
    if str(period.get("period_id") or "") == period_key:
        return True
    start_at = str(period.get("start_at") or "").strip()
    if not start_at:
        return False
    try:
        return parse_datetime(start_at, timezone).strftime("%Y-%m") == period_key
    except ValueError:
        return False


def _database(path: Path) -> PostgresDatabase:
    load_dotenv_file(path, override=True)
    if not DatabaseConfig.is_configured():
        raise ValueError("A configuração PostgreSQL não está disponível.")
    return PostgresDatabase(DatabaseConfig.from_environment())


def _main_runs(database: PostgresDatabase, *, period_id: str) -> tuple[MainRun, ...]:
    with database.connection() as connection:
        rows = connection.execute(
            f"""
            select m.run_id, m.client_id, m.tenant_id, m.period_key,
                   m.timezone, m.scope_hash, m.metric_definition_version,
                   r.execution_type, r.period_start_at, r.period_end_at,
                   r.period_mode, r.origin, r.publication_manifest_path
            from {SCHEMA_NAME}.report_main_references m
            join {SCHEMA_NAME}.report_runs r on r.run_id = m.run_id
            where m.reference_kind = 'MONTHLY'
              and m.period_key = %s
              and r.deleted_at is null
              and r.status = 'READY_FOR_CONTROLLED_DISTRIBUTION'
            order by m.client_id, m.run_id
            """,
            (period_id,),
        ).fetchall()
    return tuple(
        MainRun(
            run_id=str(row[0]),
            client_id=str(row[1]),
            tenant_id=str(row[2]),
            period_key=str(row[3]),
            timezone=str(row[4]),
            scope_hash=str(row[5]),
            metric_definition_version=str(row[6]),
            execution_type=str(row[7]),
            period_start_at=_iso(row[8]),
            period_end_at=_iso(row[9]),
            period_mode=str(row[10]),
            origin=str(row[11]),
            manifest_path=Path(str(row[12] or "")).resolve(),
        )
        for row in rows
    )


def _profiles(root: Path) -> dict[str, tuple[Path, ClientProfile]]:
    profiles: dict[str, tuple[Path, ClientProfile]] = {}
    for path in sorted(root.glob("*.json")):
        profile = load_operational_client_profile(path)
        if profile.client_id in profiles:
            raise ValueError("Há perfis operacionais duplicados para o mesmo cliente.")
        profiles[profile.client_id] = (path.resolve(), profile)
    return profiles


def _publication_document(value: Mapping[str, Any]) -> PublicationDocument:
    return PublicationDocument(
        path=str(value.get("path") or ""),
        document_kind=str(value.get("document_kind") or ""),
        document_variant=(
            str(value["document_variant"])
            if value.get("document_variant") is not None
            else None
        ),
        tag_uuid=(str(value["tag_uuid"]) if value.get("tag_uuid") is not None else None),
        tag_category=(
            str(value["tag_category"])
            if value.get("tag_category") is not None
            else None
        ),
        tag_value=(
            str(value["tag_value"])
            if value.get("tag_value") is not None
            else None
        ),
    )


def _manifest_documents(path: Path) -> tuple[PublicationDocument, ...]:
    if not path.is_file():
        raise ValueError("Manifesto MAIN não foi localizado.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Manifesto MAIN inválido.") from exc
    values = payload.get("documents") if isinstance(payload, Mapping) else None
    if not isinstance(values, list):
        raise ValueError("Manifesto MAIN sem documentos válidos.")
    documents = tuple(
        _publication_document(item) for item in values if isinstance(item, Mapping)
    )
    selected = tuple(
        item for item in documents if item.document_kind in {"custom", "tag", "cloud"}
    )
    if sum(item.document_kind == "custom" for item in selected) != 1:
        raise ValueError("A publicação precisa ter exatamente um relatório customizado.")
    if any(not Path(item.path).resolve().is_file() for item in selected):
        raise ValueError("Um documento mensal publicado não foi localizado.")
    return selected


def _manifest_cloud_dataset_path(path: Path) -> Path:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Manifesto MAIN inválido.") from exc
    sources = payload.get("source_datasets") if isinstance(payload, Mapping) else None
    cloud = sources.get("cloud") if isinstance(sources, Mapping) else None
    if not isinstance(cloud, Mapping):
        raise ValueError("Manifesto MAIN sem dataset Cloud registrado.")
    dataset_path = Path(str(cloud.get("path") or "")).resolve()
    if not dataset_path.is_file():
        raise ValueError("Dataset Cloud publicado não foi localizado.")
    expected = str(cloud.get("sha256") or "").strip().lower()
    if expected and sha256_file(dataset_path) != expected:
        raise ValueError("Hash do dataset Cloud publicado não confere.")
    return dataset_path


def _manifest_vm_dataset_path(path: Path) -> Path:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Manifesto MAIN inválido.") from exc
    sources = payload.get("source_datasets") if isinstance(payload, Mapping) else None
    vm = sources.get("vm") if isinstance(sources, Mapping) else None
    if not isinstance(vm, Mapping):
        vm = payload.get("source_dataset") if isinstance(payload, Mapping) else None
    if not isinstance(vm, Mapping):
        raise ValueError("Manifesto MAIN sem dataset VM registrado.")
    dataset_path = Path(str(vm.get("path") or "")).resolve()
    if not dataset_path.is_file():
        raise ValueError("Dataset VM publicado não foi localizado.")
    expected = str(vm.get("sha256") or "").strip().lower()
    if expected and sha256_file(dataset_path) != expected:
        raise ValueError("Hash do dataset VM publicado não confere.")
    return dataset_path


def _execution_root_from_dataset(dataset_path: Path) -> Path:
    for parent in dataset_path.resolve().parents:
        if parent.name == "report-datasets":
            return parent.parent
    raise ValueError("Dataset VM publicado está fora da estrutura operacional esperada.")


def _restore_predecessor_compact(
    *,
    predecessor: MainRun,
    profile: ClientProfile,
    documents: Sequence[PublicationDocument],
) -> CompactFindingSnapshot:
    dataset_path = _manifest_vm_dataset_path(predecessor.manifest_path)
    output_root = _execution_root_from_dataset(dataset_path)
    snapshot_path = (
        output_root
        / "snapshots"
        / predecessor.client_id
        / predecessor.run_id
        / "tenable_vm_vulnerabilities.snapshot.json"
    )
    try:
        source_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            "Snapshot de fonte VM do predecessor não foi localizado."
        ) from exc
    completed_at = str(source_snapshot.get("completed_at") or "").strip()
    if not completed_at:
        raise ValueError("Snapshot de fonte VM do predecessor sem data de conclusão.")
    parse_datetime(completed_at, predecessor.timezone)
    references = {
        f"{document.document_kind}:{position}": str(Path(document.path).resolve())
        for position, document in enumerate(documents, start=1)
    }
    return prepare_compact_run_snapshot(
        profile=profile,
        source_run_id=predecessor.run_id,
        execution_type=predecessor.execution_type,
        period=_period(predecessor),
        output_root=output_root.resolve(),
        document_references=references,
        created_at=completed_at,
    )


def _selected_documents(
    documents: Sequence[PublicationDocument],
    *,
    tag_only: bool,
) -> tuple[PublicationDocument, ...]:
    if tag_only:
        return tuple(item for item in documents if item.document_kind == "tag")
    return tuple(documents)


def _selected_plan(
    plan: Sequence[Any],
    *,
    tag_only: bool,
) -> tuple[Any, ...]:
    if not tag_only:
        return tuple(plan)
    return tuple(
        item
        for item in plan
        if _selected_documents(item.documents, tag_only=True)
    )


def _select_clients(
    plan: Sequence[Any],
    client_ids: frozenset[str],
) -> tuple[Any, ...]:
    if not client_ids:
        return tuple(plan)
    available = {str(item.current.client_id) for item in plan}
    if not client_ids.issubset(available):
        raise ValueError("Cliente solicitado não pertence ao plano selecionado.")
    return tuple(item for item in plan if item.current.client_id in client_ids)


def _select_main_runs(
    runs: Sequence[Any],
    client_ids: frozenset[str],
) -> tuple[Any, ...]:
    if not client_ids:
        return tuple(runs)
    available = {str(item.client_id) for item in runs}
    if not client_ids.issubset(available):
        raise ValueError("Cliente solicitado não pertence aos conjuntos MAIN.")
    return tuple(item for item in runs if item.client_id in client_ids)


def _plan_from_position(plan: Sequence[Any], start_position: int) -> tuple[Any, ...]:
    if not plan and start_position == 1:
        return ()
    if start_position < 1 or start_position > len(plan):
        raise ValueError("A posição inicial precisa existir no plano selecionado.")
    return tuple(plan[start_position - 1 :])


def _matching_predecessor(
    current: MainRun,
    candidates: Sequence[MainRun],
    *,
    scope_override_clients: frozenset[str],
) -> tuple[MainRun, bool]:
    compatible_identity = tuple(
        item
        for item in candidates
        if item.client_id == current.client_id
        and item.tenant_id == current.tenant_id
        and item.timezone == current.timezone
        and item.metric_definition_version == current.metric_definition_version
    )
    exact = tuple(
        item for item in compatible_identity if item.scope_hash == current.scope_hash
    )
    if len(exact) == 1:
        return exact[0], False
    if exact:
        raise ValueError("Há mais de um predecessor MAIN com o mesmo escopo.")
    if current.client_id not in scope_override_clients:
        raise ValueError("A competência anterior existe apenas com escopo diferente.")
    if len(compatible_identity) != 1:
        raise ValueError("A exceção de escopo exige um único predecessor MAIN.")
    return compatible_identity[0], True


def build_plan(
    database: PostgresDatabase,
    *,
    period_id: str,
    profiles_root: Path,
    scope_override_clients: frozenset[str],
    selected_client_ids: frozenset[str] = frozenset(),
) -> tuple[RepairPlanItem, ...]:
    current_runs = _select_main_runs(
        _main_runs(database, period_id=period_id),
        selected_client_ids,
    )
    previous_runs = _main_runs(database, period_id=_previous_month(period_id))
    if not current_runs:
        raise ValueError("Nenhum conjunto MAIN foi encontrado para a competência.")
    profile_map = _profiles(profiles_root)
    compact_repository = PostgresCompactSnapshotRepository(database, migrate=False)
    registry = PostgresReportRegistry(database, migrate=False)
    planned: list[RepairPlanItem] = []
    overridden: set[str] = set()
    for current in current_runs:
        predecessor, controlled = _matching_predecessor(
            current,
            previous_runs,
            scope_override_clients=scope_override_clients,
        )
        if controlled:
            overridden.add(current.client_id)
        profile_entry = profile_map.get(current.client_id)
        if profile_entry is None:
            raise ValueError("Perfil operacional do cliente MAIN não foi localizado.")
        profile_path, profile = profile_entry
        if profile.tenant_id != current.tenant_id:
            raise ValueError("Perfil operacional diverge do tenant do conjunto MAIN.")
        compact = compact_repository.find_run(
            client_id=current.client_id,
            tenant_id=current.tenant_id,
            run_id=current.run_id,
        )
        if compact is None:
            raise ValueError("Snapshot compacto da competência atual não foi localizado.")
        replay_compact_snapshot(compact)
        current_report = registry.get_report(current.run_id)
        predecessor_report = registry.get_report(predecessor.run_id)
        if current_report.snapshot is None or predecessor_report.snapshot is None:
            raise ValueError("Snapshot histórico MAIN não foi localizado.")
        predecessor_documents = _manifest_documents(predecessor.manifest_path)
        predecessor_compact = compact_repository.find_run(
            client_id=predecessor.client_id,
            tenant_id=predecessor.tenant_id,
            run_id=predecessor.run_id,
        )
        if predecessor_compact is None:
            predecessor_compact = _restore_predecessor_compact(
                predecessor=predecessor,
                profile=profile,
                documents=predecessor_documents,
            )
        planned.append(
            RepairPlanItem(
                current=current,
                predecessor=predecessor,
                profile_path=profile_path,
                profile=profile,
                compact_snapshot=compact,
                predecessor_compact_snapshot=predecessor_compact,
                current_snapshot=current_report.snapshot,
                predecessor_snapshot=predecessor_report.snapshot,
                documents=_manifest_documents(current.manifest_path),
                controlled_scope_override=controlled,
            )
        )
    if overridden != set(scope_override_clients):
        raise ValueError("Uma exceção solicitada não corresponde a uma divergência de escopo.")
    return tuple(planned)


def _active_conflicts(
    database: PostgresDatabase,
    plan: Sequence[RepairPlanItem],
) -> int:
    client_ids = sorted({item.current.client_id for item in plan})
    with database.connection() as connection:
        row = connection.execute(
            f"""
            select count(*)
            from {SCHEMA_NAME}.web_batch_jobs
            where client_id = any(%s) and status = any(%s)
            """,
            (client_ids, list(ACTIVE_JOB_STATUSES)),
        ).fetchone()
    return int(row[0] or 0)


def _period(run: MainRun) -> ReportingPeriod:
    return ReportingPeriod(
        start_at=parse_datetime(run.period_start_at, run.timezone),
        end_at=parse_datetime(run.period_end_at, run.timezone),
        timezone=run.timezone,
        mode=PeriodMode(run.period_mode),
        reference_at=parse_datetime(run.period_end_at, run.timezone),
    )


def _backup(
    plan: Sequence[RepairPlanItem],
    *,
    backup_root: Path,
    period_id: str,
    tag_only: bool,
) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = (
        backup_root / f"monthly-comparisons-{period_id}-{timestamp}"
    ).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    index: list[dict[str, Any]] = []
    for position, item in enumerate(plan, start=1):
        item_root = destination / f"{position:03d}"
        item_root.mkdir()
        manifest_backup = item_root / "publication-manifest.json"
        shutil.copy2(item.current.manifest_path, manifest_backup)
        documents = []
        for document_position, document in enumerate(
            _selected_documents(item.documents, tag_only=tag_only),
            start=1,
        ):
            source = Path(document.path).resolve()
            target = item_root / f"{document_position:03d}.docx"
            shutil.copy2(source, target)
            documents.append({"source": str(source), "backup": str(target)})
        index.append(
            {
                "manifest_source": str(item.current.manifest_path),
                "manifest_backup": str(manifest_backup),
                "documents": documents,
            }
        )
    write_json_atomic(
        destination / "backup-index.json",
        {
            "operation": OPERATION,
            "period_id": period_id,
            "created_at": datetime.now(UTC).isoformat(),
            "items": index,
        },
    )
    return destination


def _validate_rebuilt_history(
    *,
    item: RepairPlanItem,
    current: HistorySnapshot,
) -> None:
    if current.compatibility.scope_hash != item.current.scope_hash:
        raise ValueError("O escopo reconstruído diverge do MAIN publicado.")
    stored = item.current_snapshot
    if current.summary != stored.summary:
        raise ValueError("As métricas reconstruídas divergem do snapshot histórico atual.")
    if current.open_finding_keys != stored.open_finding_keys:
        raise ValueError("Os findings abertos reconstruídos divergem do histórico atual.")
    if current.fixed_finding_keys != stored.fixed_finding_keys:
        raise ValueError("Os findings corrigidos reconstruídos divergem do histórico atual.")
    if current.resurfaced_finding_keys != stored.resurfaced_finding_keys:
        raise ValueError("Os findings ressurgidos reconstruídos divergem do histórico atual.")


def _validate_predecessor_identity(
    predecessor: MainRun,
    rebuilt: HistorySnapshot,
) -> None:
    if rebuilt.run_id != predecessor.run_id:
        raise ValueError("O histórico predecessor reconstruído diverge da execução MAIN.")
    expected_start = parse_datetime(
        predecessor.period_start_at,
        predecessor.timezone,
    )
    expected_end = parse_datetime(
        predecessor.period_end_at,
        predecessor.timezone,
    )
    rebuilt_start = parse_datetime(rebuilt.period_start_at, predecessor.timezone)
    rebuilt_end = parse_datetime(rebuilt.period_end_at, predecessor.timezone)
    if rebuilt_start != expected_start or rebuilt_end != expected_end:
        raise ValueError(
            "O histórico predecessor reconstruído diverge das fronteiras MAIN."
        )


def _rebuild_predecessor_snapshot(
    item: RepairPlanItem,
    *,
    output_root: Path,
) -> HistorySnapshot:
    compact = item.predecessor_compact_snapshot
    if compact is None:
        return item.predecessor_snapshot
    materialized = materialize_compact_snapshot_run(
        snapshot=compact,
        profile=item.profile,
        run_id=item.predecessor.run_id,
        output_root=output_root,
    )
    artifact = build_report_dataset_from_snapshot(
        profile=item.profile,
        run_id=item.predecessor.run_id,
        period=_period(item.predecessor),
        output_root=output_root,
        include_output=item.profile.presentation.vm_top5_include_output,
        execution_type=item.predecessor.execution_type,
    )
    tag_bundle = build_tag_report_datasets_from_snapshot(
        profile=item.profile,
        run_id=item.predecessor.run_id,
        period=_period(item.predecessor),
        output_root=output_root,
        include_output=item.profile.presentation.vm_top5_include_output,
        execution_type=item.predecessor.execution_type,
    )
    payload = json.loads(artifact.dataset_path.read_text(encoding="utf-8"))
    tag_payloads = tuple(
        json.loads(tag_artifact.dataset_path.read_text(encoding="utf-8"))
        for tag_artifact in tag_bundle.artifacts
    )
    rebuilt = build_history_snapshot(
        profile=item.profile,
        dataset=payload,
        dataset_path=artifact.dataset_path,
        normalized_findings_path=materialized.findings_path,
        tag_datasets=tag_payloads,
    )
    _validate_predecessor_identity(item.predecessor, rebuilt)
    return replace(
        rebuilt,
        snapshot_id=item.predecessor_snapshot.snapshot_id,
        compatibility=item.predecessor_snapshot.compatibility,
    )


def _create_item_work_directory(work_root: Path) -> Path:
    work_root.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        candidate = work_root / uuid.uuid4().hex[:8]
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError("Não foi possível reservar diretório temporário da manutenção.")


def _apply_item(
    item: RepairPlanItem,
    *,
    database: PostgresDatabase,
    template: Path,
    work_root: Path,
    applied_at: str,
    tag_only: bool,
) -> int:
    item_work = _create_item_work_directory(work_root)
    staging = item.current.manifest_path.parent / f".monthly-comparison-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        predecessor_snapshot = _rebuild_predecessor_snapshot(
            item,
            output_root=item_work,
        )
        materialized = materialize_compact_snapshot_run(
            snapshot=item.compact_snapshot,
            profile=item.profile,
            run_id=item.current.run_id,
            output_root=item_work,
        )
        period = _period(item.current)
        artifact = build_report_dataset_from_snapshot(
            profile=item.profile,
            run_id=item.current.run_id,
            period=period,
            output_root=item_work,
            include_output=item.profile.presentation.vm_top5_include_output,
            execution_type=item.current.execution_type,
        )
        tag_bundle = build_tag_report_datasets_from_snapshot(
            profile=item.profile,
            run_id=item.current.run_id,
            period=period,
            output_root=item_work,
            include_output=item.profile.presentation.vm_top5_include_output,
            execution_type=item.current.execution_type,
        )
        tag_paths = {
            artifact.tag.uuid: artifact.dataset_path for artifact in tag_bundle.artifacts
        }
        override = (
            HistoryComparisonOverride(
                predecessor=predecessor_snapshot,
                allowed_scope_changes=("vm_include_unlicensed",),
                reason=CONTROLLED_SCOPE_REASON,
                notice=CONTROLLED_SCOPE_NOTICE,
            )
            if item.controlled_scope_override
            else None
        )
        base_registry = PostgresReportRegistry(database, migrate=False)
        registry = _SnapshotOverlayRegistry(base_registry, predecessor_snapshot)
        prepared = prepare_dataset_history(
            profile=item.profile,
            dataset_path=artifact.dataset_path,
            normalized_findings_path=materialized.findings_path,
            output_path=artifact.directory / "report-dataset-with-history.json",
            tag_dataset_paths=tag_paths,
            registry=registry,
            repository=PostgresSnapshotRepository(database, migrate=False),
            origin=item.current.origin,
            comparison_override=override,
        )
        _validate_rebuilt_history(item=item, current=prepared.current)
        selected_documents = _selected_documents(item.documents, tag_only=tag_only)
        by_kind = {
            document.document_kind: document
            for document in selected_documents
            if document.document_kind == "custom"
        }
        replacements: list[PublicationDocumentReplacement] = []
        if "custom" in by_kind:
            custom_destination = by_kind["custom"]
            custom_staged = staging / "custom.docx"
            generate_customizations_report(
                template_path=template,
                dataset_path=prepared.enriched_dataset_path,
                profile=item.profile,
                output_path=custom_staged,
                mask_sensitive=False,
            )
            replacements.append(
                PublicationDocumentReplacement(
                    staged_path=custom_staged,
                    destination=custom_destination,
                )
            )
        tag_artifacts = {value.tag.uuid: value for value in tag_bundle.artifacts}
        tag_position = 0
        for destination in selected_documents:
            if destination.document_kind != "tag":
                continue
            tag_position += 1
            tag_uuid = str(destination.tag_uuid or "")
            if tag_uuid not in tag_artifacts or tag_uuid not in prepared.tag_enriched_dataset_paths:
                raise ValueError("TAG publicada não foi reconstruída pelo snapshot compacto.")
            staged = staging / f"tag-{tag_position:03d}.docx"
            refresh_tag_temporal_comparison(
                source_path=destination.path,
                dataset_path=prepared.tag_enriched_dataset_paths[tag_uuid],
                profile=item.profile,
                output_path=staged,
                mask_sensitive=False,
            )
            replacements.append(
                PublicationDocumentReplacement(
                    staged_path=staged,
                    destination=destination,
                )
            )
        cloud_destinations = tuple(
            document
            for document in selected_documents
            if document.document_kind == "cloud"
        )
        if len(cloud_destinations) > 1:
            raise ValueError("A publicação possui mais de um relatório Cloud.")
        if cloud_destinations:
            cloud_destination = cloud_destinations[0]
            cloud_dataset = load_cloud_report_dataset(
                _manifest_cloud_dataset_path(item.current.manifest_path)
            )
            cloud_period = cloud_dataset.get("period")
            if not isinstance(cloud_period, Mapping):
                raise ValueError("Dataset Cloud publicado sem período válido.")
            if not _cloud_dataset_matches_period(
                cloud_period,
                period_key=item.current.period_key,
                timezone=item.current.timezone,
            ):
                raise ValueError("Dataset Cloud publicado pertence a outra competência.")
            cloud_request = CloudExecutionRequest(
                profile=item.profile,
                period=period,
                execution_type=item.current.execution_type,
                run_id=item.current.run_id,
                attempt_number=1,
                output_root=item_work,
                report_directory=staging,
                template_path=template,
            )
            cloud_repository = PostgresCloudSnapshotRepository(database, migrate=False)
            cloud_history = [
                _history_row(replay_cloud_snapshot(snapshot).dataset)
                for snapshot in cloud_repository.list_monthly_main_before(
                    compatibility=cloud_request.compatibility(),
                    period_id_before=item.current.period_key,
                )
            ]
            cloud_enriched = _with_monthly_history(cloud_dataset, cloud_history)
            cloud_dataset_staged = write_json_atomic(
                staging / "cloud-dataset-with-history.json",
                cloud_enriched,
            )
            cloud_staged = staging / "cloud.docx"
            generate_cloud_report(
                template_path=template,
                dataset_path=cloud_dataset_staged,
                profile=item.profile,
                output_path=cloud_staged,
                variant=cloud_destination.document_variant or "expanded",
                translator=build_default_text_translator(),
                mask_sensitive=False,
            )
            replacements.append(
                PublicationDocumentReplacement(
                    staged_path=cloud_staged,
                    destination=cloud_destination,
                )
            )
        operations = PostgresOperationsRepository(database, migrate=False)
        refresh_publication_documents_atomically(
            manifest_path=item.current.manifest_path,
            replacements=tuple(replacements),
            audit_key="monthly_comparison_repair",
            audit_metadata={
                "operation": OPERATION,
                "applied_at": applied_at,
                "period_id": item.current.period_key,
                "document_count": len(replacements),
                "history_status": prepared.history_status,
                "controlled_scope_override": item.controlled_scope_override,
                "tag_only": tag_only,
                **(
                    {
                        "allowed_scope_changes": ["vm_include_unlicensed"],
                        "reason": CONTROLLED_SCOPE_REASON,
                    }
                    if item.controlled_scope_override
                    else {}
                ),
            },
            commit_callback=lambda: operations.record_publication_manifest(
                item.current.manifest_path
            ),
        )
        return len(replacements)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(item_work, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconstrói comparativos mensais de documentos MAIN usando snapshots "
            "compactos, sem nova coleta Tenable."
        )
    )
    parser.add_argument("--period-id", required=True)
    parser.add_argument(
        "--database-env-file",
        type=Path,
        default=ROOT / "credentials" / "database.env",
    )
    parser.add_argument(
        "--profiles-root",
        type=Path,
        default=ROOT / "clients" / "managed",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=ROOT / "templates" / "corporate" / "base-v1.docx",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=ROOT / "data" / "maintenance-backups",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=ROOT / "data" / "maintenance-work" / "monthly-comparisons",
    )
    parser.add_argument("--scope-override-client", action="append", default=[])
    parser.add_argument(
        "--client-id",
        action="append",
        default=[],
        help="Restringe a manutenção a um cliente do plano; pode ser repetido.",
    )
    parser.add_argument("--tag-only", action="store_true")
    parser.add_argument(
        "--start-position",
        type=int,
        default=1,
        help="Retoma a partir da posição N do plano determinístico já confirmado.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation")
    args = parser.parse_args(argv)

    database = _database(args.database_env_file)
    overrides = frozenset(
        str(value).strip() for value in args.scope_override_client if str(value).strip()
    )
    selected_clients = frozenset(
        str(value).strip() for value in args.client_id if str(value).strip()
    )
    plan = build_plan(
        database,
        period_id=args.period_id,
        profiles_root=args.profiles_root,
        scope_override_clients=overrides,
        selected_client_ids=selected_clients,
    )
    plan = _selected_plan(plan, tag_only=args.tag_only)
    plan = _plan_from_position(plan, args.start_position)
    conflicts = _active_conflicts(database, plan)
    summary = {
        "operation": OPERATION,
        "period_id": args.period_id,
        "mode": "apply" if args.apply else "dry-run",
        "main_count": len(plan),
        "document_count": sum(
            len(_selected_documents(item.documents, tag_only=args.tag_only))
            for item in plan
        ),
        "custom_document_count": sum(
            document.document_kind == "custom"
            for item in plan
            for document in item.documents
        ),
        "tag_document_count": sum(
            document.document_kind == "tag"
            for item in plan
            for document in item.documents
        ),
        "cloud_document_count": sum(
            document.document_kind == "cloud"
            for item in plan
            for document in item.documents
        ),
        "controlled_scope_override_count": sum(
            item.controlled_scope_override for item in plan
        ),
        "predecessor_compact_snapshot_count": sum(
            item.predecessor_compact_snapshot is not None for item in plan
        ),
        "tag_only": args.tag_only,
        "active_conflicts": conflicts,
        "applied": False,
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not args.apply:
        return 0
    expected = f"REPARAR COMPARATIVOS {args.period_id}"
    if args.confirmation != expected:
        raise ValueError(f'Digite exatamente "{expected}" para aplicar.')
    if conflicts:
        raise ValueError("Há execução ativa para cliente incluído; aguarde antes de aplicar.")
    if not args.template.is_file():
        raise ValueError("Template oficial não encontrado.")
    backup = _backup(
        plan,
        backup_root=args.backup_root,
        period_id=args.period_id,
        tag_only=args.tag_only,
    )
    args.work_root.mkdir(parents=True, exist_ok=True)
    applied_at = datetime.now(UTC).isoformat()
    applied_documents = 0
    for position, item in enumerate(plan, start=1):
        print(
            json.dumps(
                {
                    "status": "PROCESSING",
                    "position": position,
                    "total": len(plan),
                    "document_count": len(
                        _selected_documents(item.documents, tag_only=args.tag_only)
                    ),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        applied_documents += _apply_item(
            item,
            database=database,
            template=args.template.resolve(),
            work_root=args.work_root.resolve(),
            applied_at=applied_at,
            tag_only=args.tag_only,
        )
    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "main_count": len(plan),
                "document_count": applied_documents,
                "backup_created": backup.is_dir(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
