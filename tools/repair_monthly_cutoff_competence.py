from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from tenable_reports.application.monthly_cutoff_repair import (
    MONTHLY_CUTOFF_MODE,
    is_monthly_cutoff_interval,
    monthly_competence_document_path,
    needs_monthly_cutoff_repair,
    rewrite_monthly_cutoff_checkpoint,
    rewrite_monthly_cutoff_manifest,
)
from tenable_reports.application.component_collection import (
    ComponentCollectionCheckpoint,
)
from tenable_reports.application.publishing import write_json_atomic
from tenable_reports.application.staged_execution import CollectionCheckpoint
from tenable_reports.config.environment import load_dotenv_file
from tenable_reports.domain.report_reference import (
    READY_STATUS,
    ReportCandidate,
    ReportOrigin,
    reference_key_for_candidate,
)
from tenable_reports.infrastructure.postgresql import (
    SCHEMA_NAME,
    DatabaseConfig,
    PostgresDatabase,
    _jsonb,
)


ACTIVE_JOB_STATUSES = (
    "QUEUED",
    "RUNNING",
    "WAITING_WAS_DECISION",
    "INTERRUPT_REQUESTED",
)


@dataclass(frozen=True, slots=True)
class DocumentMove:
    source: Path
    target: Path


@dataclass(frozen=True, slots=True)
class RepairRun:
    run_id: str
    client_id: str
    tenant_id: str
    origin: str
    execution_type: str
    period_start_at: str
    period_end_at: str
    timezone: str
    scope_hash: str
    metric_definition_version: str
    status: str
    metadata: Mapping[str, Any]
    manifest_path: Path
    original_manifest: Mapping[str, Any]
    updated_manifest: Mapping[str, Any]
    moves: tuple[DocumentMove, ...]
    main_reference_key: str | None
    main_set_at: Any | None


@dataclass(frozen=True, slots=True)
class CheckpointRepair:
    client_id: str
    path: Path
    original: Mapping[str, Any]
    updated: Mapping[str, Any]


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def _load_database(path: Path) -> PostgresDatabase:
    loaded = load_dotenv_file(path, override=False)
    return PostgresDatabase(DatabaseConfig.from_environment(loaded))


def _candidate_key(run: RepairRun):
    try:
        origin = ReportOrigin(run.origin)
    except ValueError:
        origin = ReportOrigin.MANUAL
    return reference_key_for_candidate(
        ReportCandidate(
            run_id=run.run_id,
            client_id=run.client_id,
            tenant_id=run.tenant_id,
            origin=origin,
            execution_type=run.execution_type,
            period_start_at=run.period_start_at,
            period_end_at=run.period_end_at,
            period_mode=MONTHLY_CUTOFF_MODE,
            timezone=run.timezone,
            scope_hash=run.scope_hash,
            metric_definition_version=run.metric_definition_version,
            publication_status=run.status,
            documents_valid=True,
        )
    )


def build_plan(
    database: PostgresDatabase,
    *,
    period_id: str,
    repaired_at: str,
) -> tuple[RepairRun, ...]:
    with database.connection() as connection:
        rows = connection.execute(
            f"""
            select r.run_id, r.client_id, r.tenant_id, r.origin,
                   r.execution_type, r.period_start_at, r.period_end_at,
                   r.timezone, r.scope_hash, r.metric_definition_version,
                   r.status, r.period_id, r.period_mode, r.metadata,
                   r.publication_manifest_path,
                   m.reference_key, m.set_at
            from {SCHEMA_NAME}.report_runs r
            left join {SCHEMA_NAME}.report_main_references m on m.run_id = r.run_id
            where r.deleted_at is null and r.status = %s
            order by r.client_id, r.created_at
            """,
            (READY_STATUS,),
        ).fetchall()

        plan: list[RepairRun] = []
        targets: set[Path] = set()
        for row in rows:
            timezone_name = str(row[7] or "")
            start_at = _iso(row[5])
            end_at = _iso(row[6])
            if not is_monthly_cutoff_interval(
                period_id=period_id,
                start_at=start_at,
                end_at=end_at,
                timezone_name=timezone_name,
            ):
                continue
            manifest_path = Path(str(row[14] or "")).resolve()
            if not manifest_path.is_file():
                raise ValueError("Publicação elegível sem manifesto existente.")
            original = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(original, Mapping):
                raise ValueError("Manifesto de publicação inválido.")
            manifest_period = original.get("period")
            if not isinstance(manifest_period, Mapping):
                raise ValueError("Manifesto de publicação sem período válido.")
            if not needs_monthly_cutoff_repair(
                stored_period_id=str(row[11] or ""),
                stored_period_mode=str(row[12] or ""),
                manifest_period=manifest_period,
                target_period_id=period_id,
            ):
                continue
            moves: list[DocumentMove] = []
            path_map: dict[Path, Path] = {}
            for document in original.get("documents") or ():
                if not isinstance(document, Mapping):
                    raise ValueError("Manifesto possui documento inválido.")
                source = Path(str(document.get("path") or "")).resolve()
                if not source.is_file():
                    raise ValueError("Documento publicado não foi localizado.")
                target = monthly_competence_document_path(
                    source, period_id=period_id
                ).resolve()
                if target != source and target.exists():
                    raise ValueError("O destino SET26 já existe; correção recusada.")
                if target in targets:
                    raise ValueError("Dois documentos apontam para o mesmo destino SET26.")
                targets.add(target)
                moves.append(DocumentMove(source=source, target=target))
                path_map[source] = target
            updated = rewrite_monthly_cutoff_manifest(
                original,
                period_id=period_id,
                document_paths=path_map,
                repaired_at=repaired_at,
            )
            plan.append(
                RepairRun(
                    run_id=str(row[0]),
                    client_id=str(row[1]),
                    tenant_id=str(row[2]),
                    origin=str(row[3] or "MANUAL"),
                    execution_type=str(row[4] or ""),
                    period_start_at=start_at,
                    period_end_at=end_at,
                    timezone=timezone_name,
                    scope_hash=str(row[8] or ""),
                    metric_definition_version=str(row[9] or ""),
                    status=str(row[10] or ""),
                    metadata=dict(row[13]) if isinstance(row[13], Mapping) else {},
                    manifest_path=manifest_path,
                    original_manifest=dict(original),
                    updated_manifest=updated,
                    moves=tuple(moves),
                    main_reference_key=(str(row[15]) if row[15] else None),
                    main_set_at=row[16],
                )
            )

        for run in plan:
            db_paths = {
                Path(str(item[0])).resolve()
                for item in connection.execute(
                    f"""
                    select d.path
                    from {SCHEMA_NAME}.published_documents d
                    join {SCHEMA_NAME}.publications p
                      on p.publication_id = d.publication_id
                    where p.run_id = %s
                    """,
                    (run.run_id,),
                ).fetchall()
            }
            manifest_paths = {move.source for move in run.moves}
            if db_paths != manifest_paths:
                raise ValueError(
                    "Documentos do banco divergem do manifesto; correção recusada."
                )
    return tuple(plan)


def build_checkpoint_plan(
    database: PostgresDatabase,
    *,
    period_id: str,
) -> tuple[CheckpointRepair, ...]:
    """Find persisted execution checkpoints left with the old period identity."""

    with database.connection() as connection:
        client_rows = connection.execute(
            f"""
            select distinct client_id
            from {SCHEMA_NAME}.report_runs
            where deleted_at is null
              and period_id = %s
              and period_mode = %s
              and metadata -> 'competence_repair' ->> 'period_id' = %s
            """,
            (period_id, MONTHLY_CUTOFF_MODE, period_id),
        ).fetchall()
        client_ids = sorted(str(row[0]) for row in client_rows)
        if not client_ids:
            return ()
        rows = connection.execute(
            f"""
            select distinct j.client_id, paths.checkpoint_path
            from {SCHEMA_NAME}.web_batch_jobs j
            cross join lateral (
                select j.collection_checkpoint_path as checkpoint_path
                union
                select c.checkpoint_path
                from {SCHEMA_NAME}.web_batch_remote_components c
                where c.batch_job_id = j.id
            ) paths
            where j.client_id = any(%s)
              and paths.checkpoint_path is not null
            order by j.client_id, paths.checkpoint_path
            """,
            (client_ids,),
        ).fetchall()

    storage_root = (Path.cwd() / "data").resolve()
    repairs: list[CheckpointRepair] = []
    seen: set[Path] = set()
    for raw_client_id, raw_path in rows:
        path = Path(str(raw_path)).resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            path.relative_to(storage_root)
        except ValueError as exc:
            raise ValueError(
                "Checkpoint elegível fora do armazenamento autorizado."
            ) from exc
        original = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(original, Mapping):
            raise ValueError("Checkpoint elegível não contém um objeto JSON.")
        period = original.get("period")
        if not isinstance(period, Mapping):
            continue
        if (
            str(period.get("period_id") or "").strip() == period_id
            and str(period.get("mode") or "").strip() == MONTHLY_CUTOFF_MODE
        ):
            continue
        try:
            updated = rewrite_monthly_cutoff_checkpoint(
                original,
                period_id=period_id,
            )
        except ValueError:
            continue
        if "checkpoint_path" in updated:
            ComponentCollectionCheckpoint.from_dict(updated)
        else:
            CollectionCheckpoint.from_dict(updated)
        repairs.append(
            CheckpointRepair(
                client_id=str(raw_client_id),
                path=path,
                original=dict(original),
                updated=updated,
            )
        )
    return tuple(repairs)


def _active_conflicts(
    database: PostgresDatabase,
    plan: tuple[RepairRun, ...],
    checkpoint_plan: tuple[CheckpointRepair, ...] = (),
) -> int:
    client_ids = sorted(
        {run.client_id for run in plan}
        | {checkpoint.client_id for checkpoint in checkpoint_plan}
    )
    if not client_ids:
        return 0
    with database.connection() as connection:
        row = connection.execute(
            f"""
            select count(*) from {SCHEMA_NAME}.web_batch_jobs
            where client_id = any(%s) and status = any(%s)
            """,
            (client_ids, list(ACTIVE_JOB_STATUSES)),
        ).fetchone()
    return int(row[0] or 0)


def _replace_paths(value: Mapping[str, Any], moves: tuple[DocumentMove, ...]):
    mapping = {str(move.source): str(move.target) for move in moves}
    return {str(key): mapping.get(str(path), str(path)) for key, path in value.items()}


def _update_compact_snapshot(
    connection: Any, run: RepairRun
) -> None:
    rows = connection.execute(
        f"""
        select snapshot_id, payload_gzip, document_references
        from {SCHEMA_NAME}.compact_finding_snapshots where run_id = %s
        for update
        """,
        (run.run_id,),
    ).fetchall()
    for snapshot_id, payload_gzip, document_references in rows:
        payload = json.loads(gzip.decompress(bytes(payload_gzip)).decode("utf-8"))
        documents = _replace_paths(document_references or {}, run.moves)
        payload["document_references"] = documents
        logical = _canonical_json(payload)
        connection.execute(
            f"""
            update {SCHEMA_NAME}.compact_finding_snapshots
            set period_mode = %s, content_sha256 = %s, payload_gzip = %s,
                document_references = %s
            where snapshot_id = %s
            """,
            (
                MONTHLY_CUTOFF_MODE,
                hashlib.sha256(logical).hexdigest(),
                gzip.compress(logical, compresslevel=9, mtime=0),
                _jsonb(documents),
                snapshot_id,
            ),
        )


def _update_cloud_snapshot(
    connection: Any, run: RepairRun, *, period_id: str
) -> None:
    rows = connection.execute(
        f"""
        select snapshot_id, payload_gzip
        from {SCHEMA_NAME}.cloud_report_snapshots where run_id = %s
        for update
        """,
        (run.run_id,),
    ).fetchall()
    for snapshot_id, payload_gzip in rows:
        payload = json.loads(gzip.decompress(bytes(payload_gzip)).decode("utf-8"))
        period = payload.get("period")
        if isinstance(period, dict):
            period["period_id"] = period_id
            period["mode"] = MONTHLY_CUTOFF_MODE
        logical = _canonical_json(payload)
        connection.execute(
            f"""
            update {SCHEMA_NAME}.cloud_report_snapshots
            set period_mode = %s, content_sha256 = %s, payload_gzip = %s
            where snapshot_id = %s
            """,
            (
                MONTHLY_CUTOFF_MODE,
                hashlib.sha256(logical).hexdigest(),
                gzip.compress(logical, compresslevel=9, mtime=0),
                snapshot_id,
            ),
        )


def _update_database(
    database: PostgresDatabase,
    plan: tuple[RepairRun, ...],
    *,
    period_id: str,
) -> None:
    main_runs = [run for run in plan if run.main_reference_key]
    with database.connection() as connection:
        for run in plan:
            manifest_period = dict(run.updated_manifest["period"])
            metadata = dict(run.metadata)
            metadata["period"] = manifest_period
            metadata["competence_repair"] = dict(
                run.updated_manifest["competence_repair"]
            )
            connection.execute(
                f"""
                update {SCHEMA_NAME}.report_runs
                set period_id = %s, period_mode = %s, metadata = %s,
                    updated_at = now()
                where run_id = %s
                """,
                (period_id, MONTHLY_CUTOFF_MODE, _jsonb(metadata), run.run_id),
            )
            history_row = connection.execute(
                f"select payload from {SCHEMA_NAME}.history_snapshots "
                "where run_id = %s for update",
                (run.run_id,),
            ).fetchone()
            if history_row is not None:
                history_payload = dict(history_row[0])
                history_payload["period_id"] = period_id
                connection.execute(
                    f"""
                    update {SCHEMA_NAME}.history_snapshots
                    set period_id = %s, period_mode = %s, payload = %s
                    where run_id = %s
                    """,
                    (
                        period_id,
                        MONTHLY_CUTOFF_MODE,
                        _jsonb(history_payload),
                        run.run_id,
                    ),
                )
            publication = connection.execute(
                f"""
                select publication_id from {SCHEMA_NAME}.publications
                where run_id = %s for update
                """,
                (run.run_id,),
            ).fetchone()
            if publication is None:
                raise ValueError("Publicação ausente durante a correção.")
            publication_id = int(publication[0])
            connection.execute(
                f"""
                update {SCHEMA_NAME}.publications
                set manifest_sha256 = %s, payload = %s
                where publication_id = %s
                """,
                (
                    _sha256(run.manifest_path),
                    _jsonb(run.updated_manifest),
                    publication_id,
                ),
            )
            for move in run.moves:
                if move.source != move.target:
                    connection.execute(
                        f"""
                        update {SCHEMA_NAME}.published_documents
                        set path = %s where publication_id = %s and path = %s
                        """,
                        (str(move.target), publication_id, str(move.source)),
                    )
                    connection.execute(
                        f"update {SCHEMA_NAME}.artifacts set path = %s "
                        "where path = %s",
                        (str(move.target), str(move.source)),
                    )
            connection.execute(
                f"""
                update {SCHEMA_NAME}.artifacts
                set sha256 = %s, size_bytes = %s, last_seen_at = now()
                where path = %s
                """,
                (
                    _sha256(run.manifest_path),
                    run.manifest_path.stat().st_size,
                    str(run.manifest_path),
                ),
            )
            _update_compact_snapshot(connection, run)
            _update_cloud_snapshot(connection, run, period_id=period_id)

        if main_runs:
            connection.execute(
                f"delete from {SCHEMA_NAME}.report_main_references "
                "where run_id = any(%s)",
                ([run.run_id for run in main_runs],),
            )
        for run in main_runs:
            key = _candidate_key(run)
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.report_main_references (
                    reference_key, client_id, tenant_id, reference_kind,
                    period_key, period_mode, timezone, scope_hash,
                    metric_definition_version, run_id, set_by, set_reason, set_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          'system-monthly-cutoff-repair',
                          'MONTHLY_CUTOFF_COMPETENCE_REPAIR', %s)
                on conflict (reference_key) do update set
                    run_id = excluded.run_id,
                    set_by = excluded.set_by,
                    set_reason = excluded.set_reason,
                    set_at = excluded.set_at,
                    updated_at = now()
                """,
                (
                    key.stable_key,
                    key.client_id,
                    key.tenant_id,
                    key.kind.value,
                    key.period_key,
                    key.period_mode,
                    key.timezone,
                    key.scope_hash,
                    key.metric_definition_version,
                    run.run_id,
                    run.main_set_at,
                ),
            )
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.report_reference_events (
                    reference_key, event_type, previous_run_id, new_run_id,
                    actor, reason, payload
                ) values (%s, 'MAIN_RECLASSIFIED', %s, %s,
                          'system-monthly-cutoff-repair',
                          'MONTHLY_CUTOFF_COMPETENCE_REPAIR', %s)
                """,
                (
                    key.stable_key,
                    run.run_id,
                    run.run_id,
                    _jsonb({"period_id": period_id, "effective_end_at_preserved": True}),
                ),
            )


def apply_plan(
    database: PostgresDatabase,
    plan: tuple[RepairRun, ...],
    *,
    checkpoint_plan: tuple[CheckpointRepair, ...] = (),
    period_id: str,
    backup_root: Path,
) -> Path:
    conflicts = _active_conflicts(database, plan, checkpoint_plan)
    if conflicts:
        raise ValueError(
            "Há execução ativa para cliente incluído; aguarde antes de aplicar."
        )
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = (backup_root / f"monthly-cutoff-{period_id}-{timestamp}").resolve()
    backup_dir.mkdir(parents=True, exist_ok=False)
    write_json_atomic(
        backup_dir / "backup.json",
        {
            "period_id": period_id,
            "created_at": datetime.now(UTC).isoformat(),
            "runs": [
                {
                    "manifest_path": str(run.manifest_path),
                    "manifest": dict(run.original_manifest),
                    "moves": [
                        {"source": str(move.source), "target": str(move.target)}
                        for move in run.moves
                    ],
                }
                for run in plan
            ],
            "checkpoints": [
                {
                    "path": str(checkpoint.path),
                    "payload": dict(checkpoint.original),
                }
                for checkpoint in checkpoint_plan
            ],
        },
    )
    moved: list[DocumentMove] = []
    written: list[RepairRun] = []
    written_checkpoints: list[CheckpointRepair] = []
    try:
        for run in plan:
            for move in run.moves:
                if move.source != move.target:
                    move.source.replace(move.target)
                    moved.append(move)
            write_json_atomic(run.manifest_path, run.updated_manifest)
            written.append(run)
        for checkpoint in checkpoint_plan:
            write_json_atomic(checkpoint.path, checkpoint.updated)
            written_checkpoints.append(checkpoint)
        _update_database(database, plan, period_id=period_id)
    except Exception:
        for checkpoint in reversed(written_checkpoints):
            write_json_atomic(checkpoint.path, checkpoint.original)
        for run in reversed(written):
            write_json_atomic(run.manifest_path, run.original_manifest)
        for move in reversed(moved):
            if move.target.exists() and not move.source.exists():
                move.target.replace(move.source)
        raise
    return backup_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regulariza competência mensal com corte no último dia."
    )
    parser.add_argument("--period-id", required=True)
    parser.add_argument(
        "--database-env-file",
        type=Path,
        default=Path("credentials/database.env"),
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=Path("data/maintenance-backups"),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation")
    args = parser.parse_args(argv)
    database = _load_database(args.database_env_file)
    repaired_at = datetime.now(UTC).isoformat()
    plan = build_plan(database, period_id=args.period_id, repaired_at=repaired_at)
    checkpoint_plan = build_checkpoint_plan(database, period_id=args.period_id)
    conflicts = _active_conflicts(database, plan, checkpoint_plan)
    result: dict[str, Any] = {
        "period_id": args.period_id,
        "run_count": len(plan),
        "main_count": sum(bool(run.main_reference_key) for run in plan),
        "document_count": sum(len(run.moves) for run in plan),
        "checkpoint_count": len(checkpoint_plan),
        "active_conflicts": conflicts,
        "applied": False,
    }
    if args.apply:
        expected = f"REPARAR {args.period_id}"
        if args.confirmation != expected:
            raise ValueError(f'Digite exatamente "{expected}" para aplicar.')
        backup = apply_plan(
            database,
            plan,
            checkpoint_plan=checkpoint_plan,
            period_id=args.period_id,
            backup_root=args.backup_root,
        )
        result["applied"] = True
        result["backup_created"] = backup.is_dir()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
