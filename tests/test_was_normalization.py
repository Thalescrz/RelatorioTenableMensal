from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.collect import CollectionResult
from tenable_reports.application.normalize_was import (
    normalize_was_collection,
    normalize_was_collection_with_catalog,
)
from tenable_reports.application.plugin_catalog import (
    MemoryPluginCatalogRepository,
    build_plugin_catalog_entries,
)
from tenable_reports.config.profile import Owasp2025Mode, load_client_profile
from tenable_reports.domain.models import build_source_snapshot
from tenable_reports.domain.reporting import previous_calendar_month
from tenable_reports.domain.was import build_was_report_data, normalize_was_findings
from tenable_reports.infrastructure.jsonl_io import iter_jsonl_objects


ROOT = Path(__file__).resolve().parents[1]


def was_record(
    finding_id: str,
    *,
    plugin_id: int = 980001,
    state: str = "OPEN",
    severity: str = "CRITICAL",
    vpr_score: float | None = 9.1,
    last_found: str = "2026-07-15T10:00:00Z",
    last_fixed: str | None = None,
) -> dict:
    record = {
        "finding_id": finding_id,
        "asset": {"uuid": "application-1", "fqdn": "app.example.invalid"},
        "url": f"https://app.example.invalid/path/{finding_id}",
        "plugin": {
            "id": plugin_id,
            "name": "Finding WEB de fixture",
            "description": "Descricao de fixture.",
            "solution": "Solucao de fixture.",
            "see_also": ["https://example.invalid/advisory"],
            "owasp_2021": ["A03:2021-Injection"],
            "owasp": [{"year": "2025", "category": "A5"}],
            "vpr_v2": {"score": vpr_score} if vpr_score is not None else {},
        },
        "state": state,
        "severity": severity,
        "first_found": "2026-06-01T10:00:00Z",
        "last_found": last_found,
        "output": "evidence fixture",
        "http_method": "GET",
    }
    if last_fixed:
        record["last_fixed"] = last_fixed
    return record


class WasNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.period = previous_calendar_month(
            reference_at="2026-08-13T10:00:00-03:00",
            timezone_name="America/Fortaleza",
        )

    def test_normalizes_observed_tenant_contract(self) -> None:
        result = normalize_was_findings([was_record("was-1")], client_id="client-fixture")
        finding = result.findings[0]
        self.assertEqual(finding.source_asset_id, "application-1")
        self.assertEqual(finding.application_uri, "https://app.example.invalid")
        self.assertEqual(finding.owasp_2021, ("A03:2021-Injection",))
        self.assertEqual(finding.owasp_2025, ("A05",))
        self.assertEqual(finding.vpr_score, 9.1)

    def test_top5_only_contains_open_findings_inside_period(self) -> None:
        normalized = normalize_was_findings([
            was_record("open-in-period"),
            was_record("open-after-period", plugin_id=980002, last_found="2026-08-05T10:00:00Z"),
            was_record("fixed-in-period", state="FIXED", last_fixed="2026-07-20T10:00:00Z"),
            was_record("info", plugin_id=980003, severity="INFO"),
        ], client_id="client-fixture")
        was, top, population = build_was_report_data(
            normalized.findings,
            period=self.period,
            collected=True,
            include_info_severity=False,
            top_limit=5,
        )
        self.assertEqual(population["included"], 2)
        self.assertEqual(population["open"], 1)
        self.assertEqual(population["fixed"], 1)
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]["finding_instances"], 1)
        self.assertEqual(was["owasp"]["A05"][0]["instances"], 1)
        self.assertNotIn("A03", was["owasp"])

    def test_owasp_rows_include_risk_and_are_ordered_by_vpr(self) -> None:
        records = [
            was_record(
                "more-instances-1",
                plugin_id=980010,
                severity="MEDIUM",
                vpr_score=4.2,
            ),
            was_record(
                "more-instances-2",
                plugin_id=980010,
                severity="HIGH",
                vpr_score=6.1,
            ),
            was_record(
                "more-instances-3",
                plugin_id=980010,
                severity="MEDIUM",
                vpr_score=5.0,
            ),
            was_record(
                "highest-vpr",
                plugin_id=980020,
                severity="HIGH",
                vpr_score=9.4,
            ),
            was_record(
                "without-vpr-1",
                plugin_id=980030,
                severity="CRITICAL",
                vpr_score=None,
            ),
            was_record(
                "without-vpr-2",
                plugin_id=980030,
                severity="CRITICAL",
                vpr_score=None,
            ),
        ]
        normalized = normalize_was_findings(records, client_id="client-fixture")

        was, _, _ = build_was_report_data(
            normalized.findings,
            period=self.period,
            collected=True,
            include_info_severity=False,
            top_limit=5,
        )

        rows = was["owasp"]["A05"]
        self.assertEqual(
            [row["plugin_id"] for row in rows],
            [980020, 980010, 980030],
        )
        self.assertEqual(rows[0]["severity"], "HIGH")
        self.assertEqual(rows[0]["vpr_score"], 9.4)
        self.assertEqual(rows[1]["severity"], "HIGH")
        self.assertEqual(rows[1]["vpr_score"], 6.1)
        self.assertEqual(rows[1]["instances"], 3)
        self.assertIsNone(rows[2]["vpr_score"])

    def test_exclusive_owasp_rows_replace_inclusive_catalog_membership(self) -> None:
        multiclasse = was_record("multiclasse", plugin_id=980040, vpr_score=8.0)
        multiclasse["plugin"]["owasp"] = [
            {"year": "2025", "category": "A1"},
            {"year": "2025", "category": "A2"},
        ]
        normalized = normalize_was_findings([multiclasse], client_id="client-fixture")
        exclusive_rows = {
            f"A{index:02d}": [] for index in range(1, 11)
        }
        exclusive_rows["A02"] = [{
            "category": "A02",
            "plugin_id": 980040,
            "name": "Finding WEB de fixture",
            "family": "Fixture",
            "severity": "HIGH",
            "vpr_score": 8.0,
            "instances": 7,
        }]

        was, _, _ = build_was_report_data(
            normalized.findings,
            period=self.period,
            collected=True,
            include_info_severity=False,
            top_limit=5,
            owasp_2025_mode=Owasp2025Mode.CONSOLE_EXCLUSIVE,
            exclusive_owasp_rows=exclusive_rows,
        )

        self.assertEqual(was["owasp"]["A01"], [])
        self.assertEqual([row["plugin_id"] for row in was["owasp"]["A02"]], [980040])
        self.assertEqual(was["owasp"]["A02"][0]["instances"], 7)
        self.assertEqual(
            was["owasp_selection"]["source"],
            "console_analysis_exclusive",
        )
        self.assertEqual(was["owasp_selection"]["coverage"], "COMPLETE")

    def test_exclusive_owasp_rows_are_sorted_by_vpr_descending(self) -> None:
        exclusive_rows = {f"A{index:02d}": [] for index in range(1, 11)}
        exclusive_rows["A01"] = [
            {
                "category": "A01",
                "plugin_id": 3,
                "name": "Sem VPR",
                "family": None,
                "severity": "CRITICAL",
                "vpr_score": None,
                "instances": 30,
            },
            {
                "category": "A01",
                "plugin_id": 2,
                "name": "VPR maior",
                "family": None,
                "severity": "HIGH",
                "vpr_score": 9.7,
                "instances": 1,
            },
            {
                "category": "A01",
                "plugin_id": 1,
                "name": "VPR menor",
                "family": None,
                "severity": "CRITICAL",
                "vpr_score": 6.2,
                "instances": 99,
            },
        ]

        was, _, _ = build_was_report_data(
            (),
            period=self.period,
            collected=True,
            include_info_severity=False,
            top_limit=5,
            owasp_2025_mode=Owasp2025Mode.CONSOLE_EXCLUSIVE,
            exclusive_owasp_rows=exclusive_rows,
        )

        self.assertEqual(
            [row["plugin_id"] for row in was["owasp"]["A01"]],
            [2, 1, 3],
        )

    def test_no_was_snapshot_is_not_reported_as_zero(self) -> None:
        was, top, population = build_was_report_data(
            (), period=self.period, collected=False, include_info_severity=False, top_limit=5
        )
        self.assertEqual(was["availability"], "NOT_COLLECTED")
        self.assertEqual(top, ())
        self.assertEqual(population["input"], 0)

    def test_owasp_2025_coverage_distinguishes_partial_catalog_from_no_findings(self) -> None:
        classified = was_record("classified", plugin_id=980001)
        unclassified = was_record("detail-unavailable", plugin_id=980002)
        unclassified["plugin"].pop("owasp")
        normalized = normalize_was_findings(
            [classified, unclassified], client_id="client-fixture"
        )

        was, _, _ = build_was_report_data(
            normalized.findings,
            period=self.period,
            collected=True,
            include_info_severity=False,
            top_limit=5,
        )

        self.assertEqual(was["owasp_2025_coverage"]["status"], "PARTIAL")
        self.assertEqual(was["owasp_2025_coverage"]["plugin_ids_total"], 2)
        self.assertEqual(was["owasp_2025_coverage"]["plugin_ids_observed"], 1)

    def test_normalizes_tenant_owasp_categories_without_leading_zero(self) -> None:
        records = []
        for index in range(1, 11):
            record = was_record(f"owasp-{index}", plugin_id=980000 + index)
            record["plugin"]["owasp"] = [{"year": "2025", "category": f"A{index}"}]
            records.append(record)
        normalized = normalize_was_findings(records, client_id="client-fixture")
        was, _, _ = build_was_report_data(
            normalized.findings,
            period=self.period,
            collected=True,
            include_info_severity=False,
            top_limit=5,
        )
        self.assertEqual(sorted(was["owasp"]), [f"A{index:02d}" for index in range(1, 11)])
        self.assertEqual(was["owasp_catalog_version"], "2025")

    def test_application_writes_compressed_was_artifact_with_storage_metrics(self) -> None:
        profile = load_client_profile(
            ROOT / "clients/examples/client-profile-intelligence-expanded.json"
        )
        records = [was_record("was-compressed")]
        snapshot = build_source_snapshot(
            run_id="run-was-compressed",
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            source="tenable_was_findings",
            export_uuid="was-export-compressed",
            query={},
            chunks=[(1, json.dumps(records).encode("utf-8"))],
            record_count=1,
            started_at="2026-08-12T00:00:00Z",
            collector_version="test",
            raw_manifest_uri="file:///was-manifest.json",
        )
        collection = CollectionResult(
            snapshot=snapshot,
            snapshot_path=Path("was.snapshot.json"),
            raw_manifest_path=Path("was.manifest.json"),
            records=tuple(records),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = normalize_was_collection(
                profile=profile,
                collection=collection,
                output_root=directory,
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(result.findings_path.name, "was-findings.jsonl.gz")
            self.assertEqual(manifest["artifact"]["compression"], "gzip")
            self.assertEqual(
                manifest["artifact"]["stored_bytes"],
                result.findings_path.stat().st_size,
            )
            self.assertGreater(manifest["artifact"]["logical_bytes"], 0)
            self.assertEqual(len(list(iter_jsonl_objects(result.findings_path))), 1)

    def test_application_persists_catalog_enriched_was_family(self) -> None:
        profile = load_client_profile(
            ROOT / "clients/examples/client-profile-intelligence-expanded.json"
        )
        records = [was_record("was-catalog")]
        snapshot = build_source_snapshot(
            run_id="run-was-catalog",
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            source="tenable_was_findings",
            export_uuid="was-export-catalog",
            query={},
            chunks=[(1, json.dumps(records).encode("utf-8"))],
            record_count=1,
            started_at="2026-08-12T00:00:00Z",
            collector_version="test",
            raw_manifest_uri="file:///was-manifest.json",
        )
        collection = CollectionResult(
            snapshot=snapshot,
            snapshot_path=Path("was.snapshot.json"),
            raw_manifest_path=Path("was.manifest.json"),
            records=tuple(records),
        )
        repository = MemoryPluginCatalogRepository()
        repository.upsert(build_plugin_catalog_entries(
            [{
                "plugin_id": 980001,
                "name": "Finding WEB de fixture",
                "family": "Web Servers",
            }],
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            source="tenable_was_plugins",
        ))

        with tempfile.TemporaryDirectory() as directory:
            result = normalize_was_collection(
                profile=profile,
                collection=collection,
                output_root=directory,
                plugin_catalog=repository,
            )

            stored = list(iter_jsonl_objects(result.findings_path))
            self.assertEqual(stored[0]["plugin_family"], "Web Servers")

    def test_application_fetches_was_catalog_before_persisting_family(self) -> None:
        profile = load_client_profile(
            ROOT / "clients/examples/client-profile-intelligence-expanded.json"
        )
        records = [was_record("was-live-catalog")]
        snapshot = build_source_snapshot(
            run_id="run-was-live-catalog",
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            source="tenable_was_findings",
            export_uuid="was-export-live-catalog",
            query={},
            chunks=[(1, json.dumps(records).encode("utf-8"))],
            record_count=1,
            started_at="2026-08-12T00:00:00Z",
            collector_version="test",
            raw_manifest_uri="file:///was-manifest.json",
        )
        collection = CollectionResult(
            snapshot=snapshot,
            snapshot_path=Path("was.snapshot.json"),
            raw_manifest_path=Path("was.manifest.json"),
            records=tuple(records),
        )
        repository = MemoryPluginCatalogRepository()

        class PluginClient:
            requested: list[int]

            def __init__(self) -> None:
                self.requested = []

            def get_was_plugin_details(self, plugin_id: int):
                self.requested.append(plugin_id)
                return {
                    "plugin_id": 980001,
                    "name": "Finding WEB de fixture",
                    "family": {"name": "Web Servers"},
                    "cwe": ["79"],
                    "owasp": [{"year": "2025", "category": "A05"}],
                }

        client = PluginClient()
        with tempfile.TemporaryDirectory() as directory:
            result, warning = normalize_was_collection_with_catalog(
                profile=profile,
                collection=collection,
                output_root=directory,
                plugin_catalog=repository,
                plugin_client=client,
            )

            stored = list(iter_jsonl_objects(result.findings_path))
            self.assertEqual(client.requested, [980001])
            self.assertEqual(stored[0]["plugin_family"], "Web Servers")
            self.assertEqual(stored[0]["cwes"], ["79"])
            self.assertEqual(stored[0]["owasp_2025"], ["A05"])
            self.assertTrue(stored[0]["owasp_detail_observed"])
            self.assertIsNone(warning)


if __name__ == "__main__":
    unittest.main()
