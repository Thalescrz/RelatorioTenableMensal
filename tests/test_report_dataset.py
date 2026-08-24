from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from tenable_reports.domain.normalization import normalize_and_link
from tenable_reports.domain.report_dataset import (
    AssetPopulationReason,
    FindingPopulationReason,
    build_report_dataset,
)
from tenable_reports.domain.reporting import previous_calendar_month
from tests.test_normalization import fixture_assets, fixture_findings


def normalized_fixture():
    return normalize_and_link(
        asset_records=fixture_assets(),
        finding_records=fixture_findings(),
        client_id="client-fixture",
    )


class ReportDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.period = previous_calendar_month(
            reference_at="2026-08-12T10:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        normalized = normalized_fixture()
        self.asset = replace(
            normalized.assets[0],
            first_scan_at="2026-06-01T10:00:00Z",
            last_scan_at="2026-08-10T10:00:00Z",
        )
        self.stale_asset = replace(
            normalized.assets[1],
            lifecycle=normalized.assets[0].lifecycle,
            terminated_at=None,
            first_scan_at="2026-01-01T10:00:00Z",
            last_scan_at="2026-06-30T23:59:59Z",
        )
        self.finding = replace(
            normalized.findings[0],
            asset_key=self.asset.asset_key,
            source_asset_id=self.asset.source_asset_id,
            state="OPEN",
            severity="CRITICAL",
            first_found_at="2026-06-15T10:00:00Z",
            last_found_at="2026-07-15T10:00:00Z",
            exploitable=True,
            vpr_score=9.8,
        )

    def build(self, assets, findings, query=None, include_info=False, tag_scope=None):
        return build_report_dataset(
            client_id="client-fixture",
            run_id="run-fixture",
            period=self.period,
            assets=assets,
            findings=findings,
            generated_at=datetime(2026, 8, 12, tzinfo=UTC),
            collection_completed_at=datetime(2026, 8, 12, tzinfo=UTC),
            finding_query=query,
            include_info_severity=include_info,
            top_assets_limit=10,
            top_vulnerabilities_limit=5,
            late_collection_grace_days=1,
            tag_scope=tag_scope,
        )

    def test_finding_in_period_can_prove_asset_observed_despite_later_last_scan(self) -> None:
        result = self.build([self.asset, self.stale_asset], [self.finding])
        self.assertEqual(
            result.asset_population_reason[self.asset.asset_key],
            AssetPopulationReason.OBSERVED_BY_FINDING,
        )
        self.assertEqual(
            result.asset_population_reason[self.stale_asset.asset_key],
            AssetPopulationReason.EXCLUDED_STALE_BEFORE_PERIOD,
        )
        self.assertEqual(result.dataset.metrics["assets"]["observed_in_period"], 1)
        self.assertEqual(result.dataset.metrics["non_mitigated"]["total"], 1)

    def test_first_seen_after_period_is_excluded_even_with_historical_finding(self) -> None:
        future_asset = replace(
            self.asset,
            first_scan_at="2026-08-02T10:00:00Z",
            last_scan_at="2026-08-10T10:00:00Z",
        )
        result = self.build([future_asset], [self.finding])
        self.assertEqual(
            result.asset_population_reason[future_asset.asset_key],
            AssetPopulationReason.EXCLUDED_FIRST_SEEN_AFTER_PERIOD,
        )
        self.assertEqual(
            result.finding_population_reason[self.finding.finding_key],
            FindingPopulationReason.EXCLUDED_ASSET_NOT_OBSERVED,
        )
        self.assertEqual(result.dataset.metrics["non_mitigated"]["total"], 0)

    def test_info_is_excluded_by_default_and_included_explicitly(self) -> None:
        info = replace(self.finding, finding_key="finding-info", severity="INFO")
        default = self.build([self.asset], [info])
        included = self.build([self.asset], [info], include_info=True)
        self.assertEqual(
            default.finding_population_reason[info.finding_key],
            FindingPopulationReason.EXCLUDED_INFO,
        )
        self.assertEqual(default.dataset.metrics["non_mitigated"]["total"], 0)
        self.assertEqual(included.dataset.metrics["non_mitigated"]["total"], 1)

    def test_fixed_is_not_reported_as_zero_when_state_was_not_collected(self) -> None:
        result = self.build(
            [self.asset],
            [self.finding],
            query={"filters": {"state": ["OPEN", "REOPENED"]}},
        )
        self.assertEqual(result.dataset.metrics["mitigated"]["availability"], "NOT_COLLECTED")
        self.assertIsNone(result.dataset.metrics["mitigated"]["total"])
        codes = {issue.code for issue in result.dataset.quality_issues}
        self.assertIn("FIXED_STATE_NOT_COLLECTED", codes)

    def test_fixed_in_period_is_counted_when_collected(self) -> None:
        fixed = replace(
            self.finding,
            finding_key="finding-fixed",
            state="FIXED",
            last_fixed_at="2026-07-20T10:00:00Z",
        )
        result = self.build(
            [self.asset],
            [fixed],
            query={"filters": {"state": ["OPEN", "REOPENED", "FIXED"]}},
        )
        self.assertEqual(result.dataset.metrics["mitigated"]["availability"], "AVAILABLE")
        self.assertEqual(result.dataset.metrics["mitigated"]["total"], 1)

    def test_top_assets_exploitable_is_subset_and_info_does_not_inflate_total(self) -> None:
        high = replace(
            self.finding,
            finding_key="finding-high",
            plugin_id=100010,
            severity="HIGH",
            exploitable=False,
        )
        unknown = replace(
            self.finding,
            finding_key="finding-medium",
            plugin_id=100011,
            severity="MEDIUM",
            exploitable=None,
        )
        info = replace(
            self.finding,
            finding_key="finding-info",
            plugin_id=100012,
            severity="INFO",
        )
        result = self.build([self.asset], [self.finding, high, unknown, info])
        row = result.dataset.top_assets[0]
        self.assertEqual(row["critical"], 1)
        self.assertEqual(row["high"], 1)
        self.assertEqual(row["medium"], 1)
        self.assertEqual(row["total"], 3)
        self.assertEqual(row["exploitable"], 1)
        self.assertEqual(row["exploitability_unknown"], 1)
        self.assertLessEqual(row["exploitable"], row["total"])

    def test_top_five_contains_public_reference_links(self) -> None:
        result = self.build([self.asset], [self.finding])
        references = result.dataset.top_open_vulnerabilities[0]["reference_urls"]
        self.assertIn(
            f"https://www.tenable.com/plugins/nessus/{self.finding.plugin_id}",
            references,
        )
        self.assertIn("https://example.invalid/advisory", references)

    def test_exploit_framework_matrix_uses_individual_tenable_flags(self) -> None:
        finding = replace(
            self.finding,
            exploit_frameworks=("Canvas", "Metasploit"),
        )
        result = self.build([self.asset], [finding])
        rows = result.dataset.metrics["by_exploit_framework"]
        self.assertEqual(
            rows,
            [
                {"framework": "Canvas", "total": 1, "critical": 1, "high": 0, "medium": 0},
                {"framework": "Metasploit", "total": 1, "critical": 1, "high": 0, "medium": 0},
            ],
        )

    def test_operating_system_matrix_uses_the_five_legacy_itp_groups(self) -> None:
        def asset(suffix: str, operating_system: str):
            return replace(
                self.asset,
                asset_key=f"client-fixture:asset-{suffix}",
                source_asset_id=f"asset-{suffix}",
                operating_systems=(operating_system,),
            )

        windows_22631 = asset("windows-22631", "Microsoft Windows 11 Pro 10.0.22631")
        windows_26100 = asset("windows-26100", "Microsoft Windows 11 Pro 10.0.26100")
        linux = asset("linux", "Ubuntu 24.04 LTS")
        mac = asset("mac", "macOS 15.0")
        web = asset("web", "Debian GNU/Linux 12")
        device = asset("device", "Network appliance")

        def finding(
            suffix: str,
            target,
            *,
            family: str,
            name: str,
            exploitable: bool = False,
            has_patch: bool = False,
        ):
            return replace(
                self.finding,
                finding_key=f"finding-{suffix}",
                source_asset_id=target.source_asset_id,
                asset_key=target.asset_key,
                plugin_id=200000 + len(suffix),
                plugin_family=family,
                plugin_name=name,
                exploitable=exploitable,
                has_patch=has_patch,
            )

        findings = [
            finding(
                "windows-22631",
                windows_22631,
                family="Windows",
                name="Windows finding",
                exploitable=True,
                has_patch=True,
            ),
            finding(
                "windows-26100",
                windows_26100,
                family="Windows : Microsoft Bulletins",
                name="Windows bulletin",
            ),
            finding(
                "linux",
                linux,
                family="Ubuntu Local Security Checks",
                name="Linux finding",
            ),
            finding(
                "mac",
                mac,
                family="MacOS X Local Security Checks",
                name="macOS finding",
            ),
            finding(
                "web",
                web,
                family="Web Servers",
                name="Web finding",
            ),
            finding(
                "device",
                device,
                family="General",
                name="Remote service detection",
            ),
        ]
        fixed_windows = replace(
            finding(
                "fixed-windows",
                windows_22631,
                family="Windows",
                name="Fixed Windows finding",
            ),
            state="FIXED",
            last_fixed_at="2026-07-20T10:00:00Z",
        )

        result = self.build(
            [windows_22631, windows_26100, linux, mac, web, device],
            [*findings, fixed_windows],
            query={"filters": {"state": ["OPEN", "REOPENED", "FIXED"]}},
        )

        self.assertEqual(
            result.dataset.metrics["by_operating_system"]["rows"],
            [
                {
                    "operating_system": "Windows",
                    "non_mitigated": 2,
                    "mitigated": 1,
                    "exploitable": 1,
                    "patch_available_over_30_days": 1,
                },
                {
                    "operating_system": "Mac OS X",
                    "non_mitigated": 1,
                    "mitigated": 0,
                    "exploitable": 0,
                    "patch_available_over_30_days": 0,
                },
                {
                    "operating_system": "Linux/Unix",
                    "non_mitigated": 1,
                    "mitigated": 0,
                    "exploitable": 0,
                    "patch_available_over_30_days": 0,
                },
                {
                    "operating_system": "WEB",
                    "non_mitigated": 1,
                    "mitigated": 0,
                    "exploitable": 0,
                    "patch_available_over_30_days": 0,
                },
                {
                    "operating_system": "Devices/Services",
                    "non_mitigated": 1,
                    "mitigated": 0,
                    "exploitable": 0,
                    "patch_available_over_30_days": 0,
                },
            ],
        )

    def test_operating_system_provenance_describes_the_legacy_itp_filters(self) -> None:
        result = self.build([self.asset], [self.finding])
        provenance = result.dataset.table_provenance["tables"]["by_operating_system"]

        self.assertEqual(provenance["group_by"], "família do plugin")
        self.assertEqual(
            provenance["rule"],
            "Windows, Mac OS X, Linux/Unix e WEB por Plugin Family; "
            "Devices/Services por Plugin Name contendo service; categorias independentes",
        )

    def test_operating_system_matrix_preserves_no_occurrences_availability(self) -> None:
        result = self.build([self.asset], [])

        matrix = result.dataset.metrics["by_operating_system"]
        self.assertEqual(matrix["availability"], "NO_DATA")
        self.assertEqual(
            [row["operating_system"] for row in matrix["rows"]],
            ["Windows", "Mac OS X", "Linux/Unix", "WEB", "Devices/Services"],
        )

    def test_tag_snapshot_does_not_filter_the_general_report_population(self) -> None:
        other_asset = replace(
            self.asset,
            asset_key="client-fixture:asset-general-b",
            source_asset_id="asset-general-b",
        )
        other_finding = replace(
            self.finding,
            finding_key="finding-general-b",
            asset_key=other_asset.asset_key,
            source_asset_id=other_asset.source_asset_id,
            plugin_id=self.finding.plugin_id + 1,
        )
        result = self.build(
            [self.asset, other_asset],
            [self.finding, other_finding],
            tag_scope={
                "category_name": "Rede",
                "selected_tags": [{
                    "uuid": "tag-rede-a",
                    "category_name": "Rede",
                    "value": "Segmento A",
                    "asset_ids": [self.asset.source_asset_id],
                }],
            },
        )
        customizations = result.dataset.customizations or {}
        rows = customizations["network_tag_snapshots"][0]["assets"]
        self.assertEqual(len(result.dataset.top_assets), 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_asset_id"], self.asset.source_asset_id)
        self.assertEqual(rows[0]["exploitable"], 1)
        self.assertFalse(
            result.dataset.source_coverage["general_collection_filtered_by_tags"]
        )
        self.assertTrue(
            result.dataset.source_coverage["tag_comparison_snapshot_available"]
        )

    def test_late_collection_is_explicit_quality_warning(self) -> None:
        result = self.build([self.asset], [self.finding])
        self.assertEqual(result.dataset.collection_timing["status"], "LATE")
        self.assertIn(
            "COLLECTION_AFTER_MONTH_CLOSE_GRACE",
            {issue.code for issue in result.dataset.quality_issues},
        )

    def test_manual_dataset_does_not_claim_an_automatic_schedule(self) -> None:
        result = build_report_dataset(
            client_id="client-fixture",
            run_id="run-manual",
            execution_type="MANUAL",
            period=self.period,
            assets=[self.asset],
            findings=[self.finding],
            generated_at=datetime(2026, 8, 12, tzinfo=UTC),
            collection_completed_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        self.assertEqual(result.dataset.execution_type, "MANUAL")
        self.assertIsNone(result.dataset.collection_timing["recommended_schedule"])

    def test_table_provenance_uses_the_same_event_dates_as_the_metrics(self) -> None:
        fixed = replace(
            self.finding,
            finding_key="finding-fixed-provenance",
            state="FIXED",
            last_fixed_at="2026-07-20T10:00:00Z",
        )
        reopened = replace(
            self.finding,
            finding_key="finding-reopened-provenance",
            state="REOPENED",
            resurfaced_at="2026-07-10T10:00:00Z",
        )
        result = self.build(
            [self.asset],
            [self.finding, fixed, reopened],
            query={"filters": {"state": ["OPEN", "REOPENED", "FIXED"]}},
        )
        tables = result.dataset.table_provenance["tables"]

        assert tables["overview"]["validation_queries"] == [
            {
                "label": "Não mitigadas",
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
            },
            {
                "label": "Mitigadas",
                "states": ["FIXED"],
                "date_fields": ["Last Fixed"],
            },
        ]
        assert tables["top_fixed_vulnerabilities"]["date_fields"] == ["Last Fixed"]
        assert tables["top_resurfaced_vulnerabilities"]["date_fields"] == [
            "Last Seen",
            "Resurfaced Date",
        ]
        assert tables["aging_by_severity"]["date_fields"] == ["Last Seen"]
        assert tables["plugin_family"]["states"] == ["FIXED"]
        assert tables["plugin_family"]["date_fields"] == ["Last Fixed"]
        assert tables["container_images"]["platform_validation_available"] is False


if __name__ == "__main__":
    unittest.main()
