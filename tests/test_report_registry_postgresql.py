from __future__ import annotations

import unittest

from tenable_reports.application.report_registry import MainDeletionRequiresDecision
from tenable_reports.infrastructure.report_registry_postgresql import (
    PostgresReportRegistry,
)


class _Cursor:
    def __init__(self, row=None, rows=()) -> None:
        self.row = row
        self.rows = rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


def _report_row(run_id: str):
    return (
        run_id,
        "cliente-a",
        "tenant-a",
        "MANUAL",
        "MANUAL",
        "2026-07-01T03:00:00Z",
        "2026-08-01T03:00:00Z",
        "CUSTOM_DATE_RANGE",
        "America/Fortaleza",
        "scope-a",
        "v1",
        "READY_FOR_CONTROLLED_DISTRIBUTION",
        None,
        None,
        None,
        {"documents_valid": True},
        None,
        None,
        None,
        None,
        None,
    )


class _Connection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.rows = {
            "run-old": _report_row("run-old"),
            "run-new": _report_row("run-new"),
        }
        self.main_run_id = "run-old"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters=()):
        normalized = " ".join(str(query).split()).lower()
        params = tuple(parameters)
        self.statements.append((normalized, params))
        if normalized.startswith("select r.run_id"):
            return _Cursor(self.rows[str(params[0])])
        if (
            normalized.startswith("select run_id from tenable_reports.report_main_references")
            and "for update" in normalized
        ):
            return _Cursor((self.main_run_id,))
        return _Cursor()


class _Database:
    def __init__(self) -> None:
        self.connection_value = _Connection()

    def connection(self):
        return self.connection_value


class PostgresReportRegistryHardDeleteTests(unittest.TestCase):
    def test_hard_delete_requires_replacement_before_any_delete_statement(self) -> None:
        database = _Database()
        registry = PostgresReportRegistry(database, migrate=False)

        with self.assertRaises(MainDeletionRequiresDecision):
            registry.hard_delete(
                "run-old",
                actor="analista",
                reason="conjunto inválido",
            )

        self.assertFalse(any(
            statement.startswith("delete ")
            for statement, _params in database.connection_value.statements
        ))

    def test_hard_delete_repoints_main_and_removes_every_run_owned_row(self) -> None:
        database = _Database()
        registry = PostgresReportRegistry(database, migrate=False)

        registry.hard_delete(
            "run-old",
            actor="analista",
            reason="conjunto inválido",
            replacement_run_id="run-new",
        )

        statements = [item[0] for item in database.connection_value.statements]
        expected_deletes = (
            "tenable_reports.report_reference_events",
            "tenable_reports.history_snapshots",
            "tenable_reports.compact_finding_snapshots",
            "tenable_reports.artifacts",
            "tenable_reports.publications",
            "tenable_reports.events",
            "tenable_reports.report_runs",
        )
        for table in expected_deletes:
            self.assertTrue(
                any(statement.startswith(f"delete from {table}") for statement in statements),
                table,
            )
        update_index = next(
            index for index, statement in enumerate(statements)
            if statement.startswith("update tenable_reports.report_main_references")
        )
        report_delete_index = next(
            index for index, statement in enumerate(statements)
            if statement.startswith("delete from tenable_reports.report_runs")
        )
        self.assertLess(update_index, report_delete_index)


if __name__ == "__main__":
    unittest.main()
