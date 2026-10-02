from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from dataclasses import dataclass
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
from tenable_reports.application.compact_snapshots import (  # noqa: E402
    CompactFindingSnapshot,
    replay_compact_snapshot,
)
from tenable_reports.application.history import (  # noqa: E402
    HistoryComparisonOverride,
    prepare_dataset_history,
)
from tenable_reports.application.publishing import (  # noqa: E402
    PublicationDocument,
    PublicationDocumentReplacement,
    refresh_publication_documents_atomically,
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
from tenable_reports.infrastructure.postgresql import (  # noqa: E402
    SCHEMA_NAME,
    PostgresDatabase,
    PostgresOperationsRepository,
    PostgresSnapshotRepository,
)
from tenable_reports.infrastructure.report_registry_postgresql import (  # noqa: E402
    PostgresReportRegistry,
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
    current_snapshot: HistorySnapshot
    predecessor_snapshot: HistorySnapshot
    documents: tuple[PublicationDocument, ...]
    controlled_scope_override: bool


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
        item for item in documents if item.document_kind in {"custom", "tag"}
    )
    if sum(item.document_kind == "custom" for item in selected) != 1:
        raise ValueError("A publicação precisa ter exatamente um relatório customizado.")
    if any(not Path(item.path).resolve().is_file() for item in selected):
        raise ValueError("Um documento mensal publicado não foi localizado.")
    return selected


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
) -> tuple[RepairPlanItem, ...]:
    current_runs = _main_runs(database, period_id=period_id)
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
        planned.append(
            RepairPlanItem(
                current=current,
                predecessor=predecessor,
                profile_path=profile_path,
                profile=profile,
                compact_snapshot=compact,
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
        for document_position, document in enumerate(item.documents, start=1):
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


def _apply_item(
    item: RepairPlanItem,
    *,
    database: PostgresDatabase,
    template: Path,
    work_root: Path,
    applied_at: str,
) -> int:
    item_work = work_root / uuid.uuid4().hex
    item_work.mkdir(parents=True, exist_ok=False)
    staging = item.current.manifest_path.parent / f".monthly-comparison-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
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
                predecessor=item.predecessor_snapshot,
                allowed_scope_changes=("vm_include_unlicensed",),
                reason=CONTROLLED_SCOPE_REASON,
                notice=CONTROLLED_SCOPE_NOTICE,
            )
            if item.controlled_scope_override
            else None
        )
        prepared = prepare_dataset_history(
            profile=item.profile,
            dataset_path=artifact.dataset_path,
            normalized_findings_path=materialized.findings_path,
            output_path=artifact.directory / "report-dataset-with-history.json",
            tag_dataset_paths=tag_paths,
            registry=PostgresReportRegistry(database, migrate=False),
            repository=PostgresSnapshotRepository(database, migrate=False),
            origin=item.current.origin,
            comparison_override=override,
        )
        _validate_rebuilt_history(item=item, current=prepared.current)
        by_kind = {
            document.document_kind: document
            for document in item.documents
            if document.document_kind == "custom"
        }
        replacements: list[PublicationDocumentReplacement] = []
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
        for destination in item.documents:
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
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation")
    args = parser.parse_args(argv)

    database = _database(args.database_env_file)
    overrides = frozenset(
        str(value).strip() for value in args.scope_override_client if str(value).strip()
    )
    plan = build_plan(
        database,
        period_id=args.period_id,
        profiles_root=args.profiles_root,
        scope_override_clients=overrides,
    )
    conflicts = _active_conflicts(database, plan)
    summary = {
        "operation": OPERATION,
        "period_id": args.period_id,
        "mode": "apply" if args.apply else "dry-run",
        "main_count": len(plan),
        "document_count": sum(len(item.documents) for item in plan),
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
        "controlled_scope_override_count": sum(
            item.controlled_scope_override for item in plan
        ),
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
    backup = _backup(plan, backup_root=args.backup_root, period_id=args.period_id)
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
                    "document_count": len(item.documents),
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
