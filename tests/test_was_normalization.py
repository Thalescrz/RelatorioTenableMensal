from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.collect import CollectionResult
from tenable_reports.application.normalize_was import normalize_was_collection
from tenable_reports.config.profile import load_client_profile
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
            "vpr_v2": {"score": 9.1},
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
        self.assertEqual(was["owasp"]["A03"][0]["instances"], 1)

    def test_no_was_snapshot_is_not_reported_as_zero(self) -> None:
        was, top, population = build_was_report_data(
            (), period=self.period, collected=False, include_info_severity=False, top_limit=5
        )
        self.assertEqual(was["availability"], "NOT_COLLECTED")
        self.assertEqual(top, ())
        self.assertEqual(population["input"], 0)

    def test_normalizes_tenant_owasp_categories_without_leading_zero(self) -> None:
        records = []
        for index in range(1, 11):
            record = was_record(f"owasp-{index}", plugin_id=980000 + index)
            record["plugin"]["owasp_2021"] = [f"A{index}"]
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


if __name__ == "__main__":
    unittest.main()
