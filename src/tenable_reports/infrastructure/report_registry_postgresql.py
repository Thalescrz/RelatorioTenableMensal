from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from tenable_reports.application.report_registry import (
    IncompatibleReference,
    MainDeletionRequiresDecision,
    MainReport,
    ReferenceEvent,
    RegisteredReport,
    ReportNotEligible,
    _required,
)
from tenable_reports.domain.history import HistorySnapshot
from tenable_reports.domain.report_reference import (
    ReportCandidate,
    ReportOrigin,
    ReportReferenceKey,
    main_eligibility,
    reference_key_for_candidate,
)
from tenable_reports.infrastructure.postgresql import (
    SCHEMA_NAME,
    PostgresDatabase,
    _history_snapshot_from_storage,
    _jsonb,
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


class PostgresReportRegistry:
    """Transactional PostgreSQL implementation of the report registry."""

    def __init__(self, database: PostgresDatabase, *, migrate: bool = True) -> None:
        self.database = database
        if migrate:
            self.database.apply_migrations()

    def register_report(
        self,
        candidate: ReportCandidate,
        snapshot: HistorySnapshot | None = None,
    ) -> None:
        if snapshot is not None and snapshot.run_id != candidate.run_id:
            raise ValueError("O snapshot precisa pertencer ao mesmo run do relatório.")
        with self.database.connection() as connection:
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.report_runs (
                    run_id, client_id, tenant_id, origin, execution_type,
                    period_id, period_start_at, period_end_at, period_mode,
                    timezone, scope_hash, metric_definition_version, status,
                    metadata
                ) values (
                    %s, %s, %s, %s, %s, %s, %s::timestamptz,
                    %s::timestamptz, %s, %s, %s, %s, %s, %s
                )
                on conflict (run_id) do update set
                    client_id = excluded.client_id,
                    tenant_id = excluded.tenant_id,
                    origin = excluded.origin,
                    execution_type = excluded.execution_type,
                    period_id = excluded.period_id,
                    period_start_at = excluded.period_start_at,
                    period_end_at = excluded.period_end_at,
                    period_mode = excluded.period_mode,
                    timezone = excluded.timezone,
                    scope_hash = excluded.scope_hash,
                    metric_definition_version = excluded.metric_definition_version,
                    status = excluded.status,
                    metadata = {SCHEMA_NAME}.report_runs.metadata || excluded.metadata,
                    updated_at = now()
                """,
                (
                    candidate.run_id,
                    candidate.client_id,
                    candidate.tenant_id,
                    candidate.origin.value,
                    candidate.execution_type,
                    reference_key_for_candidate(candidate).period_key,
                    candidate.period_start_at,
                    candidate.period_end_at,
                    candidate.period_mode,
                    candidate.timezone,
                    candidate.scope_hash,
                    candidate.metric_definition_version,
                    candidate.publication_status,
                    _jsonb({"documents_valid": candidate.documents_valid}),
                ),
            )

    def _report_from_row(self, row: Any) -> RegisteredReport:
        metadata = row[15] if isinstance(row[15], Mapping) else {}
        deleted_at = _iso(row[12])
        try:
            origin = ReportOrigin(str(row[3]))
        except ValueError:
            origin = ReportOrigin.MANUAL
        candidate = ReportCandidate(
            run_id=str(row[0]),
            client_id=str(row[1]),
            tenant_id=str(row[2]),
            origin=origin,
            execution_type=str(row[4]),
            period_start_at=_iso(row[5]) or "",
            period_end_at=_iso(row[6]) or "",
            period_mode=str(row[7] or ""),
            timezone=str(row[8] or ""),
            scope_hash=str(row[9] or ""),
            metric_definition_version=str(row[10] or ""),
            publication_status=str(row[11]),
            documents_valid=bool(metadata.get("documents_valid", False)),
            deleted_at=deleted_at,
        )
        snapshot = (
            _history_snapshot_from_storage(
                row[16], row[17], row[18], row[19], row[20]
            )
            if row[16] else None
        )
        return RegisteredReport(
            candidate=candidate,
            snapshot=snapshot,
            deleted_at=deleted_at,
            deleted_by=str(row[13]) if row[13] is not None else None,
            deletion_reason=str(row[14]) if row[14] is not None else None,
        )

    def _get_report(
        self,
        connection: Any,
        run_id: str,
        *,
        for_update: bool = False,
    ) -> RegisteredReport:
        lock = " for update of r" if for_update else ""
        row = connection.execute(
            f"""
            select r.run_id, r.client_id, r.tenant_id, r.origin,
                   r.execution_type, r.period_start_at, r.period_end_at,
                   r.period_mode, r.timezone, r.scope_hash,
                   r.metric_definition_version, r.status, r.deleted_at,
                   r.deleted_by, r.deletion_reason, r.metadata, h.payload,
                   h.fingerprint_version, h.open_fingerprints,
                   h.fixed_fingerprints, h.resurfaced_fingerprints
            from {SCHEMA_NAME}.report_runs r
            left join {SCHEMA_NAME}.history_snapshots h on h.run_id = r.run_id
            where r.run_id = %s{lock}
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Relatório não encontrado: {run_id}")
        return self._report_from_row(row)

    def get_report(self, run_id: str) -> RegisteredReport:
        with self.database.connection() as connection:
            return self._get_report(connection, run_id)

    def _main_from_parts(
        self,
        key: ReportReferenceKey,
        report: RegisteredReport,
        row: Any,
    ) -> MainReport:
        return MainReport(
            key=key,
            run_id=report.run_id,
            candidate=report.candidate,
            snapshot=report.snapshot,
            set_by=str(row[1]),
            set_reason=str(row[2]),
            set_at=_iso(row[3]) or "",
        )

    def get_main(self, key: ReportReferenceKey) -> MainReport | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                select run_id, set_by, set_reason, set_at
                from {SCHEMA_NAME}.report_main_references
                where reference_key = %s
                """,
                (key.stable_key,),
            ).fetchone()
            if row is None:
                return None
            report = self._get_report(connection, str(row[0]))
            return self._main_from_parts(key, report, row)

    def get_main_snapshot(self, key: ReportReferenceKey) -> HistorySnapshot | None:
        main = self.get_main(key)
        return main.snapshot if main else None

    def list_main_snapshots_before(
        self,
        key: ReportReferenceKey,
    ) -> tuple[HistorySnapshot, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select h.payload, h.fingerprint_version, h.open_fingerprints,
                       h.fixed_fingerprints, h.resurfaced_fingerprints
                from {SCHEMA_NAME}.report_main_references m
                join {SCHEMA_NAME}.report_runs r on r.run_id = m.run_id
                join {SCHEMA_NAME}.history_snapshots h on h.run_id = m.run_id
                where m.client_id = %s and m.tenant_id = %s
                  and m.reference_kind = %s and m.period_mode = %s
                  and m.timezone = %s and m.scope_hash = %s
                  and m.metric_definition_version = %s
                  and m.period_key < %s and r.deleted_at is null
                order by h.period_end_at, h.run_id
                """,
                (
                    key.client_id,
                    key.tenant_id,
                    key.kind.value,
                    key.period_mode,
                    key.timezone,
                    key.scope_hash,
                    key.metric_definition_version,
                    key.period_key,
                ),
            ).fetchall()
        return tuple(_history_snapshot_from_storage(*row) for row in rows)

    def _validate_promotion(
        self,
        key: ReportReferenceKey,
        report: RegisteredReport,
    ) -> None:
        if report.deleted:
            raise ReportNotEligible("Relatório excluído não pode ser promovido.")
        if reference_key_for_candidate(report.candidate) != key:
            raise IncompatibleReference(
                "O relatório não pertence à mesma referência, escopo ou versão métrica."
            )
        eligibility = main_eligibility(report.candidate)
        if not eligibility.eligible:
            raise ReportNotEligible(
                "Relatório inelegível para main: " + ", ".join(eligibility.reasons)
            )

    def _insert_event(
        self,
        connection: Any,
        *,
        event_type: str,
        key: ReportReferenceKey,
        previous_run_id: str | None,
        new_run_id: str | None,
        actor: str,
        reason: str,
    ) -> None:
        connection.execute(
            f"""
            insert into {SCHEMA_NAME}.report_reference_events (
                reference_key, event_type, previous_run_id, new_run_id,
                actor, reason
            ) values (%s, %s, %s, %s, %s, %s)
            """,
            (
                key.stable_key,
                event_type,
                previous_run_id,
                new_run_id,
                actor,
                reason,
            ),
        )

    def _upsert_main(
        self,
        connection: Any,
        *,
        key: ReportReferenceKey,
        report: RegisteredReport,
        actor: str,
        reason: str,
    ) -> MainReport:
        current = connection.execute(
            f"""
            select run_id from {SCHEMA_NAME}.report_main_references
            where reference_key = %s for update
            """,
            (key.stable_key,),
        ).fetchone()
        previous_run_id = str(current[0]) if current else None
        row = connection.execute(
            f"""
            insert into {SCHEMA_NAME}.report_main_references (
                reference_key, client_id, tenant_id, reference_kind,
                period_key, period_mode, timezone, scope_hash,
                metric_definition_version, run_id, set_by, set_reason
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (reference_key) do update set
                run_id = excluded.run_id,
                set_by = excluded.set_by,
                set_reason = excluded.set_reason,
                set_at = now(),
                updated_at = now()
            returning run_id, set_by, set_reason, set_at
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
                report.run_id,
                actor,
                reason,
            ),
        ).fetchone()
        self._insert_event(
            connection,
            event_type="MAIN_PROMOTED",
            key=key,
            previous_run_id=previous_run_id,
            new_run_id=report.run_id,
            actor=actor,
            reason=reason,
        )
        return self._main_from_parts(key, report, row)

    def promote_main(
        self,
        key: ReportReferenceKey,
        run_id: str,
        *,
        actor: str,
        reason: str,
    ) -> MainReport:
        actor = _required(actor, "actor")
        reason = _required(reason, "reason")
        with self.database.connection() as connection:
            report = self._get_report(connection, run_id, for_update=True)
            self._validate_promotion(key, report)
            return self._upsert_main(
                connection,
                key=key,
                report=report,
                actor=actor,
                reason=reason,
            )

    def auto_promote_if_empty(self, key: ReportReferenceKey, run_id: str) -> bool:
        with self.database.connection() as connection:
            report = self._get_report(connection, run_id, for_update=True)
            self._validate_promotion(key, report)
            row = connection.execute(
                f"""
                insert into {SCHEMA_NAME}.report_main_references (
                    reference_key, client_id, tenant_id, reference_kind,
                    period_key, period_mode, timezone, scope_hash,
                    metric_definition_version, run_id, set_by, set_reason
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          'system', 'FIRST_ELIGIBLE_REPORT')
                on conflict (reference_key) do nothing
                returning run_id
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
                    report.run_id,
                ),
            ).fetchone()
            if row is None:
                return False
            self._insert_event(
                connection,
                event_type="MAIN_PROMOTED",
                key=key,
                previous_run_id=None,
                new_run_id=report.run_id,
                actor="system",
                reason="FIRST_ELIGIBLE_REPORT",
            )
            return True

    def soft_delete(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        replacement_run_id: str | None = None,
        allow_gap: bool = False,
    ) -> None:
        actor = _required(actor, "actor")
        reason = _required(reason, "reason")
        with self.database.connection() as connection:
            report = self._get_report(connection, run_id, for_update=True)
            if report.deleted:
                return
            key = reference_key_for_candidate(report.candidate)
            current = connection.execute(
                f"""
                select run_id from {SCHEMA_NAME}.report_main_references
                where reference_key = %s for update
                """,
                (key.stable_key,),
            ).fetchone()
            is_main = current is not None and str(current[0]) == run_id
            new_run_id: str | None = None
            if is_main and replacement_run_id:
                if replacement_run_id == run_id:
                    raise MainDeletionRequiresDecision(
                        "A substituta precisa ser uma geração diferente."
                    )
                replacement = self._get_report(
                    connection, replacement_run_id, for_update=True
                )
                self._validate_promotion(key, replacement)
                new_run_id = replacement.run_id
                connection.execute(
                    f"""
                    update {SCHEMA_NAME}.report_main_references
                    set run_id = %s, set_by = %s, set_reason = %s,
                        set_at = now(), updated_at = now()
                    where reference_key = %s
                    """,
                    (
                        new_run_id,
                        actor,
                        f"REPLACEMENT_ON_DELETE: {reason}",
                        key.stable_key,
                    ),
                )
                self._insert_event(
                    connection,
                    event_type="MAIN_PROMOTED",
                    key=key,
                    previous_run_id=run_id,
                    new_run_id=new_run_id,
                    actor=actor,
                    reason=f"REPLACEMENT_ON_DELETE: {reason}",
                )
            elif is_main and allow_gap:
                connection.execute(
                    f"delete from {SCHEMA_NAME}.report_main_references "
                    "where reference_key = %s",
                    (key.stable_key,),
                )
                self._insert_event(
                    connection,
                    event_type="MAIN_CLEARED",
                    key=key,
                    previous_run_id=run_id,
                    new_run_id=None,
                    actor=actor,
                    reason=reason,
                )
            elif is_main:
                raise MainDeletionRequiresDecision(
                    "Escolha uma geração substituta ou confirme a lacuna histórica."
                )
            connection.execute(
                f"""
                update {SCHEMA_NAME}.report_runs
                set deleted_at = now(), deleted_by = %s, deletion_reason = %s,
                    updated_at = now()
                where run_id = %s
                """,
                (actor, reason, run_id),
            )
            self._insert_event(
                connection,
                event_type="REPORT_SOFT_DELETED",
                key=key,
                previous_run_id=run_id,
                new_run_id=new_run_id,
                actor=actor,
                reason=reason,
            )

    def hard_delete(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        replacement_run_id: str | None = None,
    ) -> None:
        actor = _required(actor, "actor")
        reason = _required(reason, "reason")
        with self.database.connection() as connection:
            report = self._get_report(connection, run_id, for_update=True)
            key = reference_key_for_candidate(report.candidate)
            current = connection.execute(
                f"""
                select run_id from {SCHEMA_NAME}.report_main_references
                where reference_key = %s for update
                """,
                (key.stable_key,),
            ).fetchone()
            is_main = current is not None and str(current[0]) == run_id
            if is_main:
                if not replacement_run_id:
                    raise MainDeletionRequiresDecision(
                        "Escolha uma geração substituta antes da exclusão permanente."
                    )
                if replacement_run_id == run_id:
                    raise MainDeletionRequiresDecision(
                        "A substituta precisa ser uma geração diferente."
                    )
                replacement = self._get_report(
                    connection, replacement_run_id, for_update=True
                )
                self._validate_promotion(key, replacement)
                connection.execute(
                    f"""
                    update {SCHEMA_NAME}.report_main_references
                    set run_id = %s, set_by = %s, set_reason = %s,
                        set_at = now(), updated_at = now()
                    where reference_key = %s
                    """,
                    (
                        replacement.run_id,
                        actor,
                        f"REPLACEMENT_ON_HARD_DELETE: {reason}",
                        key.stable_key,
                    ),
                )
            elif replacement_run_id:
                raise ValueError(
                    "Uma substituta só deve ser informada ao excluir o relatório MAIN."
                )

            connection.execute(
                f"""
                delete from {SCHEMA_NAME}.report_reference_events
                where previous_run_id = %s or new_run_id = %s
                """,
                (run_id, run_id),
            )
            for table in (
                "history_snapshots",
                "compact_finding_snapshots",
                "cloud_report_snapshots",
                "artifacts",
            ):
                connection.execute(
                    f"delete from {SCHEMA_NAME}.{table} where run_id = %s",
                    (run_id,),
                )
            connection.execute(
                f"delete from {SCHEMA_NAME}.publications where run_id = %s",
                (run_id,),
            )
            connection.execute(
                f"delete from {SCHEMA_NAME}.events where report_run_id = %s",
                (run_id,),
            )
            connection.execute(
                f"delete from {SCHEMA_NAME}.report_runs where run_id = %s",
                (run_id,),
            )

    def restore(self, run_id: str, *, actor: str, reason: str) -> None:
        actor = _required(actor, "actor")
        reason = _required(reason, "reason")
        with self.database.connection() as connection:
            report = self._get_report(connection, run_id, for_update=True)
            if not report.deleted:
                return
            key = reference_key_for_candidate(report.candidate)
            connection.execute(
                f"""
                update {SCHEMA_NAME}.report_runs
                set deleted_at = null, deleted_by = null, deletion_reason = null,
                    updated_at = now()
                where run_id = %s
                """,
                (run_id,),
            )
            self._insert_event(
                connection,
                event_type="REPORT_RESTORED",
                key=key,
                previous_run_id=None,
                new_run_id=run_id,
                actor=actor,
                reason=reason,
            )

    def list_reports(
        self,
        *,
        client_id: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[RegisteredReport, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if client_id is not None:
            clauses.append("r.client_id = %s")
            params.append(client_id)
        if not include_deleted:
            clauses.append("r.deleted_at is null")
        where = " where " + " and ".join(clauses) if clauses else ""
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select r.run_id, r.client_id, r.tenant_id, r.origin,
                       r.execution_type, r.period_start_at, r.period_end_at,
                       r.period_mode, r.timezone, r.scope_hash,
                       r.metric_definition_version, r.status, r.deleted_at,
                       r.deleted_by, r.deletion_reason, r.metadata, h.payload,
                       h.fingerprint_version, h.open_fingerprints,
                       h.fixed_fingerprints, h.resurfaced_fingerprints
                from {SCHEMA_NAME}.report_runs r
                left join {SCHEMA_NAME}.history_snapshots h on h.run_id = r.run_id
                {where}
                order by r.created_at desc, r.run_id
                """,
                tuple(params),
            ).fetchall()
        return tuple(self._report_from_row(row) for row in rows)

    def reference_events(
        self,
        *,
        reference_key: str | None = None,
    ) -> tuple[ReferenceEvent, ...]:
        where = "where reference_key = %s" if reference_key else ""
        params = (reference_key,) if reference_key else ()
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select event_type, reference_key, previous_run_id, new_run_id,
                       actor, reason, event_at
                from {SCHEMA_NAME}.report_reference_events
                {where}
                order by event_at, reference_event_id
                """,
                params,
            ).fetchall()
        return tuple(
            ReferenceEvent(
                event_type=str(row[0]),
                reference_key=str(row[1]),
                previous_run_id=str(row[2]) if row[2] is not None else None,
                new_run_id=str(row[3]) if row[3] is not None else None,
                actor=str(row[4]),
                reason=str(row[5]),
                event_at=_iso(row[6]) or "",
            )
            for row in rows
        )
