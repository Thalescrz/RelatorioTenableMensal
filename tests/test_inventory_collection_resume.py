from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.collect_inventory import _collect_inventory_segment
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.reporting import explicit_reporting_period
from tenable_reports.infrastructure.tenable_inventory.client import InventoryPage
from tenable_reports.infrastructure.tenable_vm.client import ApiError


ROOT = Path(__file__).resolve().parents[1]


def record(identifier: str) -> dict[str, object]:
    return {
        "finding_detection_id": identifier,
        "asset_id": "asset-fixture-1",
        "finding_name": "Finding resumivel",
        "state": "ACTIVE",
        "severity": "HIGH",
        "last_observed_at": "2026-07-15T12:00:00Z",
    }


class _InterruptedClient:
    def __init__(self) -> None:
        self.offsets: list[int] = []

    def search_page(self, **kwargs):
        offset = int(kwargs["offset"])
        self.offsets.append(offset)
        if offset == 0:
            return InventoryPage((record("first"),), 0, 1, 2)
        raise ApiError("interrupcao simulada", status_code=500)


class _ResumeClient:
    def __init__(self) -> None:
        self.offsets: list[int] = []

    def search_page(self, **kwargs):
        offset = int(kwargs["offset"])
        self.offsets.append(offset)
        if offset != 1:
            raise AssertionError(f"offset de retomada inesperado: {offset}")
        return InventoryPage((record("second"),), 1, 1, 2)


class InventoryCollectionResumeTests(unittest.TestCase):
    def test_validated_pages_resume_from_partial_manifest(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at="2026-08-10T12:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        interrupted = _InterruptedClient()
        resumed = _ResumeClient()
        with tempfile.TemporaryDirectory() as directory:
            arguments = {
                "profile": profile,
                "period": period,
                "state": "ACTIVE",
                "segment": "inventory_active",
                "extra_properties": (),
                "output_root": directory,
                "run_id": "run-resume",
                "page_size": 1,
                "progress_callback": None,
            }
            with self.assertRaisesRegex(ApiError, "interrupcao"):
                _collect_inventory_segment(client=interrupted, **arguments)
            records = _collect_inventory_segment(client=resumed, **arguments)

        self.assertEqual(interrupted.offsets, [0, 1])
        self.assertEqual(resumed.offsets, [1])
        self.assertEqual(
            [item["finding_detection_id"] for item in records],
            ["first", "second"],
        )


if __name__ == "__main__":
    unittest.main()
