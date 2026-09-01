from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from tenable_reports.config.analysts import AnalystCatalog


FIXED_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class AnalystCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.catalog_path = Path(self.temporary_directory.name) / "analysts.json"
        self.catalog = AnalystCatalog(self.catalog_path, now=lambda: FIXED_NOW)

    def test_catalog_creates_stable_unique_analyst_and_reloads(self) -> None:
        created = self.catalog.create(display_name="Analista Um")

        self.assertTrue(created.analyst_id)
        self.assertEqual(created.display_name, "Analista Um")
        self.assertTrue(created.active)
        self.assertEqual(created.created_at, FIXED_NOW)
        self.assertEqual(created.updated_at, FIXED_NOW)
        self.assertEqual(
            AnalystCatalog(self.catalog_path).get(created.analyst_id),
            created,
        )
        self.assertFalse(self.catalog_path.with_name(".analysts.json.tmp").exists())

    def test_catalog_rejects_case_insensitive_duplicate_and_empty_name(self) -> None:
        self.catalog.create(display_name="Analista Um")

        with self.assertRaisesRegex(ValueError, "(?i)já existe"):
            self.catalog.create(display_name="  analista um  ")
        with self.assertRaisesRegex(ValueError, "vazio"):
            self.catalog.create(display_name="   ")

    def test_catalog_updates_deactivates_and_orders_by_name_then_id(self) -> None:
        second = self.catalog.create(display_name="Zeta")
        first = self.catalog.create(display_name="Beta")

        updated = self.catalog.update(
            second.analyst_id,
            display_name="Alfa",
            active=True,
        )
        deactivated = self.catalog.deactivate(first.analyst_id)

        self.assertEqual(updated.display_name, "Alfa")
        self.assertFalse(deactivated.active)
        self.assertEqual(
            [record.display_name for record in self.catalog.list()],
            ["Alfa", "Beta"],
        )

    def test_catalog_refuses_deletion_while_record_is_in_use(self) -> None:
        created = self.catalog.create(display_name="Analista Um")

        with self.assertRaisesRegex(ValueError, "em uso"):
            self.catalog.delete(created.analyst_id, is_in_use=lambda analyst_id: True)

        self.assertEqual(self.catalog.get(created.analyst_id), created)

    def test_catalog_deletes_unused_record(self) -> None:
        created = self.catalog.create(display_name="Analista Um")

        self.catalog.delete(created.analyst_id, is_in_use=lambda analyst_id: False)

        self.assertIsNone(self.catalog.get(created.analyst_id))

    def test_catalog_rejects_invalid_json_shape(self) -> None:
        self.catalog_path.write_text(
            json.dumps({"schema_version": 1, "analysts": "inválido"}),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "inválido"):
            self.catalog.list()

    def test_catalog_rejects_unknown_record_for_update_and_deactivation(self) -> None:
        with self.assertRaisesRegex(ValueError, "não encontrado"):
            self.catalog.update("ausente", display_name="Analista", active=True)
        with self.assertRaisesRegex(ValueError, "não encontrado"):
            self.catalog.deactivate("ausente")


if __name__ == "__main__":
    unittest.main()
