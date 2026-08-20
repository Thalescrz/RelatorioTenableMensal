from __future__ import annotations

import json
import gzip
import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.collect import CollectionResult
from tenable_reports.application.normalize import normalize_collections
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.models import build_source_snapshot
from tenable_reports.domain.normalization import AssetLifecycle, normalize_and_link
from tenable_reports.infrastructure.jsonl_io import iter_jsonl_objects


ROOT = Path(__file__).resolve().parents[1]


def fixture_assets() -> list[dict[str, object]]:
    return json.loads(
        (ROOT / "tests/fixtures/tenable_vm/assets-v2-chunk.json").read_text(encoding="utf-8")
    )


def fixture_findings() -> list[dict[str, object]]:
    return [
        {
            "finding_id": "finding-fixture-1",
            "asset": {
                "uuid": "asset-fixture-1",
                "ipv4": "203.0.113.99",
                "hostname": "different.invalid",
            },
            "plugin": {
                "id": 100001,
                "name": "Sanitized plugin",
                "family": "General",
                "cve": ["CVE-2026-0001"],
                "see_also": ["https://example.invalid/advisory"],
                "description": "Sanitized description",
                "solution": "Sanitized solution",
                "exploit_available": True,
                "exploit_framework_canvas": True,
                "exploit_framework_metasploit": True,
                "has_patch": True,
                "cvss3_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                "vpr": {"score": 9.1},
            },
            "port": {"port": 443, "protocol": "TCP", "service": "https"},
            "state": "OPEN",
            "severity": "CRITICAL",
            "first_found": "2026-07-01T10:00:00Z",
            "last_found": "2026-07-31T10:00:00Z",
        },
        {
            "finding_id": "finding-fixture-orphan",
            "asset": {
                "uuid": "asset-not-exported",
                "ipv4": "192.0.2.10",
                "hostname": "host-fixture-1.invalid",
            },
            "plugin": {"id": 100002, "exploit_available": False},
            "port": {"port": 0, "protocol": "TCP"},
            "state": "FIXED",
            "severity": "HIGH",
        },
    ]


class NormalizationTests(unittest.TestCase):
    def test_normalization_accepts_single_pass_generators(self) -> None:
        result = normalize_and_link(
            asset_records=(item for item in fixture_assets()),
            finding_records=(item for item in fixture_findings()),
            client_id="client-fixture",
        )
        self.assertEqual(result.reconciliation.raw_asset_records, 2)
        self.assertEqual(result.reconciliation.raw_finding_records, 2)

    def test_exploitable_uses_only_the_direct_tenable_indicator(self) -> None:
        finding = fixture_findings()[0]
        finding["plugin"]["exploit_available"] = False
        finding["plugin"]["exploited_by_malware"] = True
        finding["plugin"]["exploitability_ease"] = "Exploit available"
        result = normalize_and_link(
            asset_records=fixture_assets(),
            finding_records=[finding],
            client_id="client-fixture",
        )
        self.assertFalse(result.findings[0].exploitable)
        self.assertEqual(
            result.findings[0].exploit_frameworks,
            ("Canvas", "Metasploit"),
        )

    def test_links_only_by_tenable_uuid_and_tracks_orphans(self) -> None:
        result = normalize_and_link(
            asset_records=fixture_assets(),
            finding_records=fixture_findings(),
            client_id="client-fixture",
        )
        self.assertEqual(result.reconciliation.raw_asset_records, 2)
        self.assertEqual(result.reconciliation.normalized_assets, 2)
        self.assertEqual(result.reconciliation.normalized_findings, 2)
        self.assertEqual(result.reconciliation.linked_findings, 1)
        self.assertEqual(result.reconciliation.orphan_findings, 1)
        self.assertEqual(result.assets[0].network_id, None)
        self.assertIsNotNone(result.findings[0].asset_key)
        self.assertIsNone(result.findings[1].asset_key)
        self.assertTrue(result.findings[0].exploitable)
        self.assertEqual(
            result.findings[0].exploit_frameworks,
            ("Canvas", "Metasploit"),
        )
        self.assertFalse(result.findings[1].exploitable)
        self.assertEqual(result.findings[0].cvss_attack_vector, "NETWORK")
        self.assertIn("CVE-2026-0001", result.findings[0].cves)
        self.assertIn("https://example.invalid/advisory", result.findings[0].references)

    def test_terminated_asset_preserves_finding_state(self) -> None:
        finding = fixture_findings()[0]
        finding["asset"] = {"uuid": "asset-fixture-2"}
        finding["state"] = "OPEN"
        result = normalize_and_link(
            asset_records=fixture_assets(),
            finding_records=[finding],
            client_id="client-fixture",
        )
        self.assertEqual(result.assets[1].lifecycle, AssetLifecycle.TERMINATED)
        self.assertEqual(result.findings[0].state, "OPEN")
        self.assertIsNone(result.findings[0].last_fixed_at)

    def test_uses_real_v2_network_aliases(self) -> None:
        asset = fixture_assets()[0]
        asset["network"] = {
            "network_id": "network-fixture",
            "network_name": "network-name-fixture",
        }
        result = normalize_and_link(
            asset_records=[asset],
            finding_records=[],
            client_id="client-fixture",
        )
        self.assertEqual(result.assets[0].network_id, "network-fixture")
        self.assertEqual(result.assets[0].network_name, "network-name-fixture")

    def test_reconciliation_counts_rejections_duplicates_and_redacts_invalid_ip(self) -> None:
        assets = fixture_assets()
        duplicate = dict(assets[0])
        invalid = dict(assets[1])
        invalid["id"] = "asset-fixture-invalid-ip"
        invalid["network"] = {"ipv4s": ["sensitive-invalid-value"]}
        result = normalize_and_link(
            asset_records=[*assets, duplicate, invalid, {"name": "missing-id"}],
            finding_records=[*fixture_findings(), fixture_findings()[0]],
            client_id="client-fixture",
        )
        self.assertEqual(result.reconciliation.raw_asset_records, 5)
        self.assertEqual(result.reconciliation.normalized_assets, 3)
        self.assertEqual(result.reconciliation.duplicate_asset_records, 1)
        self.assertEqual(result.reconciliation.rejected_asset_records, 1)
        self.assertEqual(result.reconciliation.rejected_finding_records, 1)
        messages = " ".join(issue.message for issue in result.issues)
        self.assertNotIn("sensitive-invalid-value", messages)

    def test_normalized_artifacts_are_deterministic_and_manifest_is_reconciled(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        run_id = "run-normalized-fixture"
        asset_bytes = json.dumps(fixture_assets()).encode("utf-8")
        finding_bytes = json.dumps(fixture_findings()).encode("utf-8")
        asset_snapshot = build_source_snapshot(
            run_id=run_id,
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            source="tenable_vm_assets_v2",
            export_uuid="asset-export-fixture",
            query={"chunk_size": 100},
            chunks=[(1, asset_bytes)],
            record_count=2,
            started_at="2026-08-12T00:00:00Z",
            collector_version="test",
            raw_manifest_uri="file:///asset-manifest.json",
        )
        finding_snapshot = build_source_snapshot(
            run_id=run_id,
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            source="tenable_vm_vulnerabilities",
            export_uuid="finding-export-fixture",
            query={"include_plugin_output": False},
            chunks=[(1, finding_bytes)],
            record_count=2,
            started_at="2026-08-12T00:00:00Z",
            collector_version="test",
            raw_manifest_uri="file:///finding-manifest.json",
        )
        with tempfile.TemporaryDirectory() as directory:
            result = normalize_collections(
                profile=profile,
                asset_collection=CollectionResult(
                    snapshot=asset_snapshot,
                    snapshot_path=Path("asset.snapshot.json"),
                    raw_manifest_path=Path("asset.manifest.json"),
                    records=tuple(fixture_assets()),
                ),
                finding_collection=CollectionResult(
                    snapshot=finding_snapshot,
                    snapshot_path=Path("finding.snapshot.json"),
                    raw_manifest_path=Path("finding.manifest.json"),
                    records=tuple(fixture_findings()),
                ),
                output_root=directory,
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["reconciliation"]["linked_findings"], 1)
            self.assertFalse(manifest["identity_contract"]["ip_hostname_fallback"])
            self.assertEqual(len(manifest["artifacts"]["assets"]["sha256"]), 64)
            self.assertEqual(result.assets_path.name, "assets.jsonl.gz")
            self.assertEqual(result.findings_path.name, "findings.jsonl.gz")
            self.assertEqual(result.quality_issues_path.name, "quality-issues.jsonl.gz")
            self.assertEqual(manifest["artifacts"]["assets"]["compression"], "gzip")
            self.assertEqual(
                manifest["artifacts"]["assets"]["stored_bytes"],
                result.assets_path.stat().st_size,
            )
            self.assertGreater(manifest["artifacts"]["assets"]["logical_bytes"], 0)
            self.assertEqual(len(list(iter_jsonl_objects(result.assets_path))), 2)
            self.assertEqual(len(list(iter_jsonl_objects(result.findings_path))), 2)
            with self.assertRaises(FileExistsError):
                normalize_collections(
                    profile=profile,
                    asset_collection=CollectionResult(
                        snapshot=asset_snapshot,
                        snapshot_path=Path("asset.snapshot.json"),
                        raw_manifest_path=Path("asset.manifest.json"),
                        records=tuple(fixture_assets()),
                    ),
                    finding_collection=CollectionResult(
                        snapshot=finding_snapshot,
                        snapshot_path=Path("finding.snapshot.json"),
                        raw_manifest_path=Path("finding.manifest.json"),
                        records=tuple(fixture_findings()),
                    ),
                    output_root=directory,
                )

    def test_normalization_reads_gzip_chunks_from_manifest_when_records_are_not_retained(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        run_id = "run-gzip-normalization"
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            asset_chunk = directory / "assets.jsonl.gz"
            finding_chunk = directory / "findings.jsonl.gz"
            with gzip.open(asset_chunk, "wb") as stream:
                stream.write(json.dumps(fixture_assets()).encode("utf-8"))
            with gzip.open(finding_chunk, "wb") as stream:
                stream.write(json.dumps(fixture_findings()).encode("utf-8"))

            def collection(source, export_uuid, chunk_path, count):
                manifest = directory / f"{source}.manifest.json"
                manifest.write_text(json.dumps({
                    "schema_version": 2,
                    "source": source,
                    "chunks": [{
                        "chunk_id": 1,
                        "path": chunk_path.resolve().as_uri(),
                        "encoding": "gzip",
                        "complete": True,
                    }],
                }), encoding="utf-8")
                snapshot = build_source_snapshot(
                    run_id=run_id,
                    client_id=profile.client_id,
                    tenant_id=profile.tenant_id,
                    source=source,
                    export_uuid=export_uuid,
                    query={},
                    chunks=[(1, b"fixture")],
                    record_count=count,
                    started_at="2026-08-12T00:00:00Z",
                    collector_version="test",
                    raw_manifest_uri=manifest.resolve().as_uri(),
                )
                return CollectionResult(
                    snapshot=snapshot,
                    snapshot_path=directory / f"{source}.snapshot.json",
                    raw_manifest_path=manifest,
                    records=(),
                )

            result = normalize_collections(
                profile=profile,
                asset_collection=collection(
                    "tenable_vm_assets_v2", "assets", asset_chunk, 2
                ),
                finding_collection=collection(
                    "tenable_vm_vulnerabilities", "findings", finding_chunk, 2
                ),
                output_root=directory / "normalized",
            )

            self.assertEqual(result.result.reconciliation.normalized_assets, 2)
            self.assertEqual(result.result.reconciliation.normalized_findings, 2)


if __name__ == "__main__":
    unittest.main()
