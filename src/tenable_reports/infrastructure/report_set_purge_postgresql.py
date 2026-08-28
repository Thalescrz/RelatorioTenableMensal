from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from tenable_reports.application.report_registry import ReportRegistry
from tenable_reports.application.report_set_purge import ReportSetPurgeRecord
from tenable_reports.domain.report_reference import (
    main_eligibility,
    reference_key_for_candidate,
)
from tenable_reports.infrastructure.postgresql import SCHEMA_NAME, PostgresDatabase


def _filesystem_path(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return str(Path(text))
    parsed = urlsplit(text)
    if parsed.scheme == "file":
        return str(Path(url2pathname(unquote(parsed.path))))
    if parsed.scheme:
        return None
    return str(Path(text))


class PostgresReportSetPurgeRepository:
    def __init__(
        self,
        *,
        database: PostgresDatabase,
        registry: ReportRegistry,
    ) -> None:
        self.database = database
        self.registry = registry

    def describe(self, run_id: str) -> ReportSetPurgeRecord:
        report = self.registry.get_report(run_id)
        key = reference_key_for_candidate(report.candidate)
        main = self.registry.get_main(key)
        replacements = tuple(sorted(
            candidate.run_id
            for candidate in self.registry.list_reports(
                client_id=report.candidate.client_id,
                include_deleted=False,
            )
            if candidate.run_id != run_id
            and reference_key_for_candidate(candidate.candidate) == key
            and main_eligibility(candidate.candidate).eligible
        ))
        with self.database.connection() as connection:
            run_paths = connection.execute(
                f"""
                select r.dataset_path, r.publication_manifest_path
                from {SCHEMA_NAME}.report_runs r
                where r.run_id = %s
                """,
                (run_id,),
            ).fetchone()
            publication_paths = connection.execute(
                f"""
                select p.manifest_path, p.source_dataset_path, p.history_location
                from {SCHEMA_NAME}.publications p
                where p.run_id = %s
                """,
                (run_id,),
            ).fetchone()
            document_rows = connection.execute(
                f"""
                select d.path
                from {SCHEMA_NAME}.published_documents d
                join {SCHEMA_NAME}.publications p
                  on p.publication_id = d.publication_id
                where p.run_id = %s
                order by d.document_id
                """,
                (run_id,),
            ).fetchall()
            artifact_rows = connection.execute(
                f"""
                select a.path
                from {SCHEMA_NAME}.artifacts a
                where a.run_id = %s
                order by a.artifact_id
                """,
                (run_id,),
            ).fetchall()
        raw_paths = (
            *(run_paths or ()),
            *(publication_paths or ()),
            *(row[0] for row in document_rows),
            *(row[0] for row in artifact_rows),
        )
        paths: list[str] = []
        seen: set[str] = set()
        for raw_path in raw_paths:
            path = _filesystem_path(raw_path)
            if path is None or path in seen:
                continue
            paths.append(path)
            seen.add(path)
        return ReportSetPurgeRecord(
            run_id=report.run_id,
            client_id=report.candidate.client_id,
            period_id=key.period_key,
            disk_paths=tuple(paths),
            document_count=len(document_rows),
            is_main=bool(main and main.run_id == report.run_id),
            compatible_replacement_run_ids=replacements,
        )

    def purge(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        replacement_run_id: str | None,
        allow_main_gap: bool = False,
    ) -> None:
        self.registry.hard_delete(
            run_id,
            actor=actor,
            reason=reason,
            replacement_run_id=replacement_run_id,
            allow_gap=allow_main_gap,
        )
