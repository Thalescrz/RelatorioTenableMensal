from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenable_reports.application.collect import CollectionResult
from tenable_reports.application.collect_inventory import (
    collect_bounded_historical_findings,
)
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.models import build_source_snapshot
from tenable_reports.domain.normalization import normalize_assets
from tenable_reports.domain.reporting import explicit_reporting_period
from tenable_reports.infrastructure.tenable_inventory.client import InventoryPage
from tenable_reports.infrastructure.tenable_vm.client import ApiError


ROOT = Path(__file__).resolve().parents[1]


class _InventoryClient:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls: list[dict[str, Any]] = []

    def list_properties(self):
        if self.unavailable:
            raise ApiError("Inventory indisponivel", status_code=403)
        return [
            {"name": "last_observed_at", "operators": ["between"]},
            {"name": "state", "operators": ["eq"]},
        ]

    def search_page(self, **kwargs):
        self.calls.append(kwargs)
        state_filter = next(
            item for item in kwargs["filters"] if item["field"] == "state"
        )
        state = state_filter["value"]
        record = {
            "finding_detection_id": f"detection-{state.lower()}",
            "asset_id": "asset-fixture-1",
            "finding_name": f"Finding {state}",
            "state": state,
            "severity": "HIGH",
            "port": 443,
            "protocol": "TCP",
            "last_observed_at": "2026-07-15T12:00:00Z",
        }
        outside = {
            **record,
            "finding_detection_id": f"outside-{state.lower()}",
            "last_observed_at": "2026-08-02T12:00:00Z",
        }
        return InventoryPage(
            findings=(record, outside),
            offset=kwargs["offset"],
            limit=kwargs["limit"],
            total=2,
        )


def _fixed_collection(directory: Path, *, all_states: bool = False) -> CollectionResult:
    records = [
        {
            "finding_id": "fixed-in-period",
            "asset": {"uuid": "asset-fixture-1"},
            "plugin": {"id": 100003, "name": "Finding FIXED"},
            "port": {"port": 0, "protocol": "TCP"},
            "state": "FIXED",
            "severity": "MEDIUM",
            "last_fixed": "2026-07-20T12:00:00Z",
        },
        {
            "finding_id": "fixed-outside",
            "asset": {"uuid": "asset-fixture-1"},
            "plugin": {"id": 100004, "name": "Finding FIXED fora"},
            "port": {"port": 0, "protocol": "TCP"},
            "state": "FIXED",
            "severity": "LOW",
            "last_fixed": "2026-08-02T12:00:00Z",
        },
    ]
    if all_states:
        records.append({
            "finding_id": "legacy-open",
            "asset": {"uuid": "asset-fixture-1"},
            "plugin": {"id": 100005, "name": "Legacy OPEN"},
            "port": {"port": 80, "protocol": "TCP"},
            "state": "OPEN",
            "severity": "HIGH",
            "last_found": "2026-07-25T12:00:00Z",
        })
    manifest = directory / ("legacy-all.json" if all_states else "legacy-fixed.json")
    manifest.write_text(json.dumps({"chunks": []}), encoding="utf-8")
    snapshot = build_source_snapshot(
        run_id="run-hybrid",
        client_id="client-fixture",
        tenant_id="tenant-fixture",
        source="tenable_vm_vulnerabilities",
        export_uuid="legacy-export",
        query={},
        chunks=[(1, (json.dumps(records) + "\n").encode())],
        record_count=len(records),
        started_at="2026-08-10T12:00:00Z",
        collector_version="test",
        raw_manifest_uri=manifest.resolve().as_uri(),
    )
    snapshot_path = directory / "legacy.snapshot.json"
    snapshot.write_json(snapshot_path)
    return CollectionResult(snapshot, snapshot_path, manifest, tuple(records))


class InventoryCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        self.period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at="2026-08-10T12:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        assets, _, _, _ = normalize_assets(
            [{"id": "asset-fixture-1", "name": "host.invalid"}],
            client_id=self.profile.client_id,
        )
        self.assets_by_id = {item.source_asset_id: item for item in assets}

    def test_inventory_active_and_resurfaced_are_bounded_at_source_and_locally(self) -> None:
        inventory = _InventoryClient()
        progress: list[dict[str, Any]] = []
        legacy_calls: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)

            def legacy_collector(**kwargs):
                legacy_calls.append(kwargs)
                return _fixed_collection(directory)

            result = collect_bounded_historical_findings(
                inventory_client=inventory,
                vm_client=object(),
                profile=self.profile,
                period=self.period,
                assets_by_id=self.assets_by_id,
                output_root=directory,
                run_id="run-hybrid",
                fallback_policy="fail",
                legacy_collector=legacy_collector,
                progress_callback=progress.append,
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(len(inventory.calls), 2)
        self.assertEqual(
            [next(item["value"] for item in call["filters"] if item["field"] == "state") for call in inventory.calls],
            ["ACTIVE", "RESURFACED"],
        )
        for call in inventory.calls:
            bounded = next(
                item for item in call["filters"] if item["field"] == "last_observed_at"
            )
            self.assertEqual(bounded["operator"], "between")
            self.assertEqual(
                bounded["value"],
                ["2026-07-01T03:00:00Z", "2026-08-01T03:00:00Z"],
            )
        self.assertEqual(legacy_calls[0]["request"].filters["state"], ["FIXED"])
        self.assertEqual(legacy_calls[0]["request"].filters["since"], self.period.start_epoch)
        self.assertEqual(
            [item.state for item in result.findings],
            ["OPEN", "REOPENED", "FIXED"],
        )
        self.assertEqual(manifest["route"], "inventory_bounded_hybrid")
        self.assertEqual(manifest["counts"]["inventory_active"], 1)
        self.assertEqual(manifest["counts"]["inventory_resurfaced"], 1)
        self.assertEqual(manifest["counts"]["legacy_fixed"], 1)
        self.assertEqual(
            {item["segment"] for item in progress},
            {"inventory_active", "inventory_resurfaced", "legacy_fixed"},
        )

    def test_inventory_unavailable_respects_fail_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ApiError, "Inventory indisponivel"):
                collect_bounded_historical_findings(
                    inventory_client=_InventoryClient(unavailable=True),
                    vm_client=object(),
                    profile=self.profile,
                    period=self.period,
                    assets_by_id=self.assets_by_id,
                    output_root=directory,
                    run_id="run-fail",
                    fallback_policy="fail",
                    legacy_collector=lambda **kwargs: self.fail("fallback inesperado"),
                )

    def test_inventory_unavailable_can_fallback_to_bounded_local_legacy(self) -> None:
        calls: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)

            def legacy_collector(**kwargs):
                calls.append(kwargs)
                return _fixed_collection(directory, all_states=True)

            result = collect_bounded_historical_findings(
                inventory_client=_InventoryClient(unavailable=True),
                vm_client=object(),
                profile=self.profile,
                period=self.period,
                assets_by_id=self.assets_by_id,
                output_root=directory,
                run_id="run-fallback",
                fallback_policy="warn_legacy",
                legacy_collector=legacy_collector,
            )

        self.assertEqual(calls[0]["request"].filters["state"], ["OPEN", "REOPENED", "FIXED"])
        self.assertEqual([item.state for item in result.findings], ["FIXED", "OPEN"])
        self.assertEqual(result.route, "legacy_historical_fallback")
        self.assertEqual(result.warnings[0]["code"], "INVENTORY_UNAVAILABLE_LEGACY_FALLBACK")


if __name__ == "__main__":
    unittest.main()
