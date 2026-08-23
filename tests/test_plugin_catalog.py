from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tenable_reports.application.collect import (
    VulnerabilityExportRequest,
    collect_vm_snapshot,
)
from tenable_reports.application.plugin_catalog import (
    MemoryPluginCatalogRepository,
    PluginCatalogEntry,
    build_plugin_catalog_entries,
    enrich_inventory_findings,
)
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.inventory_normalization import normalize_inventory_findings
from tenable_reports.domain.normalization import normalize_assets


ROOT = Path(__file__).resolve().parents[1]


def legacy_record(*, plugin_id: int = 100001, name: str = "Plugin de exemplo"):
    return {
        "finding_id": f"legacy-{plugin_id}",
        "asset": {"uuid": "asset-fixture-1"},
        "plugin": {
            "id": plugin_id,
            "name": name,
            "family": "General",
            "synopsis": "Resumo",
            "description": "Descricao",
            "solution": "Solucao",
            "cve": ["CVE-2026-0001"],
            "see_also": ["https://example.invalid/advisory"],
            "cvss_base_score": 5.0,
            "cvss3_base_score": 9.8,
            "vpr": {"score": 9.1},
            "exploit_available": True,
            "exploit_framework_metasploit": True,
        },
    }


def unresolved_inventory_finding(name: str = "Plugin de exemplo"):
    assets, _, _, _ = normalize_assets(
        [{"id": "asset-fixture-1", "name": "host.invalid"}],
        client_id="client-a",
    )
    findings, _, _ = normalize_inventory_findings(
        [{
            "finding_detection_id": "detection-a",
            "asset_id": "asset-fixture-1",
            "finding_name": name,
            "state": "ACTIVE",
            "severity": "HIGH",
            "port": 443,
            "protocol": "TCP",
        }],
        client_id="client-a",
        assets_by_id={item.source_asset_id: item for item in assets},
    )
    return findings[0]


class _ChunkClient:
    def start_vulnerability_export(self, **kwargs: Any) -> str:
        return "catalog-export"

    def wait_for_completion(self, export_uuid: str):
        return {"status": "FINISHED"}, [1]

    def download_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        return (json.dumps(legacy_record()) + "\n").encode("utf-8")


class PluginCatalogTests(unittest.TestCase):
    def test_migration_defines_tenant_isolated_catalog_and_lookup_index(self) -> None:
        sql = (
            ROOT
            / "src/tenable_reports/infrastructure/postgresql_migrations/0005_plugin_catalog.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("create table if not exists tenable_reports.plugin_catalog", sql)
        self.assertIn("primary key (client_id, tenant_id, plugin_id)", sql)
        self.assertIn("normalized_name", sql)
        self.assertIn("plugin_catalog_name_idx", sql)
        self.assertIn("provenance jsonb", sql)

    def test_catalog_upsert_is_idempotent_and_isolated_by_client_and_tenant(self) -> None:
        repository = MemoryPluginCatalogRepository()
        entries = build_plugin_catalog_entries(
            [legacy_record()],
            client_id="client-a",
            tenant_id="tenant-a",
            source="legacy-export-a",
        )
        repository.upsert(entries)
        repository.upsert(entries)
        repository.upsert((PluginCatalogEntry(
            **{
                **entries[0].to_dict(),
                "client_id": "client-b",
                "tenant_id": "tenant-b",
            }
        ),))

        self.assertEqual(repository.count, 2)
        self.assertEqual(
            [item.plugin_id for item in repository.find_by_normalized_name(
                client_id="client-a", tenant_id="tenant-a", name="  PLUGIN   DE EXEMPLO "
            )],
            [100001],
        )
        self.assertEqual(repository.find_by_normalized_name(
            client_id="client-a", tenant_id="tenant-b", name="Plugin de exemplo"
        ), ())

    def test_unique_name_enriches_inventory_with_legacy_plugin_metadata(self) -> None:
        repository = MemoryPluginCatalogRepository()
        repository.upsert(build_plugin_catalog_entries(
            [legacy_record()],
            client_id="client-a",
            tenant_id="tenant-a",
            source="legacy-export-a",
        ))

        enriched, issues = enrich_inventory_findings(
            [unresolved_inventory_finding()],
            client_id="client-a",
            tenant_id="tenant-a",
            repository=repository,
        )

        finding = enriched[0]
        self.assertEqual(finding.plugin_id, 100001)
        self.assertEqual(finding.plugin_family, "General")
        self.assertEqual(finding.synopsis, "Resumo")
        self.assertEqual(finding.description, "Descricao")
        self.assertEqual(finding.solution, "Solucao")
        self.assertEqual(finding.cvss3_base_score, 9.8)
        self.assertEqual(finding.vpr_score, 9.1)
        self.assertTrue(finding.exploitable)
        self.assertEqual(finding.exploit_frameworks, ("Metasploit",))
        self.assertEqual(issues, ())

    def test_missing_and_ambiguous_names_are_not_guessed(self) -> None:
        repository = MemoryPluginCatalogRepository()
        repository.upsert(build_plugin_catalog_entries(
            [
                legacy_record(plugin_id=100001, name="Nome ambiguo"),
                legacy_record(plugin_id=100002, name="Nome ambiguo"),
            ],
            client_id="client-a",
            tenant_id="tenant-a",
            source="legacy-export-a",
        ))

        enriched, issues = enrich_inventory_findings(
            [
                unresolved_inventory_finding("Nome ambiguo"),
                unresolved_inventory_finding("Nome ausente"),
            ],
            client_id="client-a",
            tenant_id="tenant-a",
            repository=repository,
        )

        self.assertEqual([item.plugin_id for item in enriched], [None, None])
        self.assertEqual(
            [issue.code for issue in issues],
            ["PLUGIN_METADATA_AMBIGUOUS", "PLUGIN_METADATA_MISSING"],
        )

    def test_validated_legacy_chunk_feeds_catalog_callback(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        observed: list[list[dict[str, Any]]] = []
        with tempfile.TemporaryDirectory() as directory:
            collect_vm_snapshot(
                client=_ChunkClient(),  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-catalog",
                plugin_catalog_callback=lambda records: observed.append(list(records)),
            )

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0]["plugin"]["id"], 100001)


if __name__ == "__main__":
    unittest.main()
