from __future__ import annotations

from typing import Sequence

from tenable_reports.application.plugin_catalog import (
    PluginCatalogEntry,
    normalize_plugin_name,
)
from tenable_reports.infrastructure.postgresql import (
    SCHEMA_NAME,
    PostgresDatabase,
    _jsonb,
)


class PostgresPluginCatalogRepository:
    def __init__(self, database: PostgresDatabase, *, migrate: bool = True) -> None:
        self.database = database
        if migrate:
            self.database.apply_migrations()

    def upsert(self, entries: Sequence[PluginCatalogEntry]) -> int:
        if not entries:
            return 0
        with self.database.connection() as connection:
            for item in entries:
                connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.plugin_catalog (
                        client_id, tenant_id, plugin_id, name, normalized_name,
                        family, synopsis, description, solution, reference_values,
                        cves, cvss2_base_score, cvss3_base_score, vpr_score,
                        exploitable, exploit_frameworks, provenance, observed_at
                    ) values (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s::timestamptz
                    )
                    on conflict (client_id, tenant_id, plugin_id) do update set
                        name = coalesce(excluded.name, {SCHEMA_NAME}.plugin_catalog.name),
                        normalized_name = case
                            when excluded.normalized_name <> '' then excluded.normalized_name
                            else {SCHEMA_NAME}.plugin_catalog.normalized_name
                        end,
                        family = coalesce(excluded.family, {SCHEMA_NAME}.plugin_catalog.family),
                        synopsis = coalesce(excluded.synopsis, {SCHEMA_NAME}.plugin_catalog.synopsis),
                        description = coalesce(excluded.description, {SCHEMA_NAME}.plugin_catalog.description),
                        solution = coalesce(excluded.solution, {SCHEMA_NAME}.plugin_catalog.solution),
                        reference_values = case
                            when excluded.reference_values <> '[]'::jsonb then excluded.reference_values
                            else {SCHEMA_NAME}.plugin_catalog.reference_values
                        end,
                        cves = case
                            when excluded.cves <> '[]'::jsonb then excluded.cves
                            else {SCHEMA_NAME}.plugin_catalog.cves
                        end,
                        cvss2_base_score = coalesce(excluded.cvss2_base_score, {SCHEMA_NAME}.plugin_catalog.cvss2_base_score),
                        cvss3_base_score = coalesce(excluded.cvss3_base_score, {SCHEMA_NAME}.plugin_catalog.cvss3_base_score),
                        vpr_score = coalesce(excluded.vpr_score, {SCHEMA_NAME}.plugin_catalog.vpr_score),
                        exploitable = coalesce(excluded.exploitable, {SCHEMA_NAME}.plugin_catalog.exploitable),
                        exploit_frameworks = case
                            when excluded.exploit_frameworks <> '[]'::jsonb then excluded.exploit_frameworks
                            else {SCHEMA_NAME}.plugin_catalog.exploit_frameworks
                        end,
                        provenance = excluded.provenance,
                        observed_at = greatest(excluded.observed_at, {SCHEMA_NAME}.plugin_catalog.observed_at),
                        updated_at = now()
                    """,
                    (
                        item.client_id,
                        item.tenant_id,
                        item.plugin_id,
                        item.name,
                        item.normalized_name,
                        item.family,
                        item.synopsis,
                        item.description,
                        item.solution,
                        _jsonb(list(item.references)),
                        _jsonb(list(item.cves)),
                        item.cvss2_base_score,
                        item.cvss3_base_score,
                        item.vpr_score,
                        item.exploitable,
                        _jsonb(list(item.exploit_frameworks)),
                        _jsonb(dict(item.provenance)),
                        item.observed_at,
                    ),
                )
        return len(entries)

    def find_by_normalized_name(
        self,
        *,
        client_id: str,
        tenant_id: str,
        name: str,
    ) -> tuple[PluginCatalogEntry, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select client_id, tenant_id, plugin_id, name, normalized_name,
                       family, synopsis, description, solution, reference_values,
                       cves, cvss2_base_score, cvss3_base_score, vpr_score,
                       exploitable, exploit_frameworks, provenance, observed_at
                from {SCHEMA_NAME}.plugin_catalog
                where client_id = %s and tenant_id = %s and normalized_name = %s
                order by plugin_id
                """,
                (client_id, tenant_id, normalize_plugin_name(name)),
            ).fetchall()
        return tuple(PluginCatalogEntry(
            client_id=str(row[0]),
            tenant_id=str(row[1]),
            plugin_id=int(row[2]),
            name=str(row[3]) if row[3] is not None else None,
            normalized_name=str(row[4]),
            family=str(row[5]) if row[5] is not None else None,
            synopsis=str(row[6]) if row[6] is not None else None,
            description=str(row[7]) if row[7] is not None else None,
            solution=str(row[8]) if row[8] is not None else None,
            references=tuple(str(item) for item in (row[9] or ())),
            cves=tuple(str(item) for item in (row[10] or ())),
            cvss2_base_score=float(row[11]) if row[11] is not None else None,
            cvss3_base_score=float(row[12]) if row[12] is not None else None,
            vpr_score=float(row[13]) if row[13] is not None else None,
            exploitable=bool(row[14]) if row[14] is not None else None,
            exploit_frameworks=tuple(str(item) for item in (row[15] or ())),
            provenance=dict(row[16] or {}),
            observed_at=(
                row[17].isoformat()
                if hasattr(row[17], "isoformat")
                else str(row[17])
            ),
        ) for row in rows)
