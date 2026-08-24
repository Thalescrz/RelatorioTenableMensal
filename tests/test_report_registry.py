from __future__ import annotations

import unittest
from datetime import UTC, datetime

from tenable_reports.domain.report_reference import (
    READY_STATUS,
    ReportCandidate,
    ReportOrigin,
    reference_key_for_candidate,
)


def _registry_module():
    try:
        from tenable_reports.application import report_registry
    except ImportError as exc:
        raise AssertionError(
            "O registro de relatórios ainda não foi implementado."
        ) from exc
    return report_registry


def _candidate(run_id: str, *, month: int = 7, scope_hash: str = "scope-a") -> ReportCandidate:
    start = datetime(2026, month, 1, 3, tzinfo=UTC)
    if month == 12:
        end = datetime(2027, 1, 1, 3, tzinfo=UTC)
    else:
        end = datetime(2026, month + 1, 1, 3, tzinfo=UTC)
    return ReportCandidate(
        run_id=run_id,
        client_id="cliente-a",
        tenant_id="tenant-a",
        origin=ReportOrigin.MANUAL,
        execution_type="MANUAL",
        period_start_at=start.isoformat().replace("+00:00", "Z"),
        period_end_at=end.isoformat().replace("+00:00", "Z"),
        period_mode="CUSTOM_DATE_RANGE",
        timezone="America/Fortaleza",
        scope_hash=scope_hash,
        metric_definition_version="v1",
        publication_status=READY_STATUS,
        documents_valid=True,
    )


class ReportRegistryTests(unittest.TestCase):
    def test_manual_promotion_replaces_main_and_records_audit_event(self) -> None:
        module = _registry_module()
        registry = module.InMemoryReportRegistry()
        first = _candidate("run-a")
        second = _candidate("run-b")
        key = reference_key_for_candidate(first)
        registry.register_report(first)
        registry.register_report(second)

        registry.promote_main(key, first.run_id, actor="analista", reason="primeiro válido")
        registry.promote_main(key, second.run_id, actor="analista", reason="dados corrigidos")

        self.assertEqual(registry.get_main(key).run_id, "run-b")
        event = registry.reference_events()[-1]
        self.assertEqual(event.event_type, "MAIN_PROMOTED")
        self.assertEqual(event.previous_run_id, "run-a")
        self.assertEqual(event.new_run_id, "run-b")
        self.assertEqual(event.actor, "analista")

    def test_deleting_main_requires_replacement_or_explicit_gap(self) -> None:
        module = _registry_module()
        registry = module.InMemoryReportRegistry()
        report = _candidate("run-main")
        key = reference_key_for_candidate(report)
        registry.register_report(report)
        registry.promote_main(key, report.run_id, actor="sistema", reason="automático")

        with self.assertRaises(module.MainDeletionRequiresDecision):
            registry.soft_delete(
                report.run_id,
                actor="analista",
                reason="documento incorreto",
            )

        self.assertEqual(registry.get_main(key).run_id, report.run_id)
        self.assertFalse(registry.get_report(report.run_id).deleted)

    def test_deleting_main_with_replacement_is_atomic(self) -> None:
        module = _registry_module()
        registry = module.InMemoryReportRegistry()
        old = _candidate("run-old")
        replacement = _candidate("run-new")
        key = reference_key_for_candidate(old)
        registry.register_report(old)
        registry.register_report(replacement)
        registry.promote_main(key, old.run_id, actor="sistema", reason="automático")

        registry.soft_delete(
            old.run_id,
            actor="analista",
            reason="saída incompleta",
            replacement_run_id=replacement.run_id,
        )

        self.assertTrue(registry.get_report(old.run_id).deleted)
        self.assertEqual(registry.get_main(key).run_id, replacement.run_id)

    def test_hard_delete_main_requires_replacement_and_removes_the_record(self) -> None:
        module = _registry_module()
        registry = module.InMemoryReportRegistry()
        old = _candidate("run-old")
        replacement = _candidate("run-new")
        key = reference_key_for_candidate(old)
        registry.register_report(old)
        registry.register_report(replacement)
        registry.promote_main(key, old.run_id, actor="sistema", reason="automático")

        with self.assertRaises(module.MainDeletionRequiresDecision):
            registry.hard_delete(
                old.run_id,
                actor="analista",
                reason="conjunto inválido",
            )

        registry.hard_delete(
            old.run_id,
            actor="analista",
            reason="conjunto inválido",
            replacement_run_id=replacement.run_id,
        )

        with self.assertRaises(KeyError):
            registry.get_report(old.run_id)
        self.assertEqual(registry.get_main(key).run_id, replacement.run_id)
        self.assertTrue(all(
            event.previous_run_id != old.run_id and event.new_run_id != old.run_id
            for event in registry.reference_events()
        ))

    def test_hard_delete_rejects_an_incompatible_replacement(self) -> None:
        module = _registry_module()
        registry = module.InMemoryReportRegistry()
        old = _candidate("run-old")
        incompatible = _candidate("run-other", scope_hash="scope-b")
        key = reference_key_for_candidate(old)
        registry.register_report(old)
        registry.register_report(incompatible)
        registry.promote_main(key, old.run_id, actor="sistema", reason="automático")

        with self.assertRaises(module.IncompatibleReference):
            registry.hard_delete(
                old.run_id,
                actor="analista",
                reason="conjunto inválido",
                replacement_run_id=incompatible.run_id,
            )

    def test_restore_never_repromotes_report_automatically(self) -> None:
        module = _registry_module()
        registry = module.InMemoryReportRegistry()
        old = _candidate("run-old")
        replacement = _candidate("run-new")
        key = reference_key_for_candidate(old)
        registry.register_report(old)
        registry.register_report(replacement)
        registry.promote_main(key, old.run_id, actor="sistema", reason="automático")
        registry.soft_delete(
            old.run_id,
            actor="analista",
            reason="substituído",
            replacement_run_id=replacement.run_id,
        )

        registry.restore(old.run_id, actor="analista", reason="recuperado para consulta")

        self.assertFalse(registry.get_report(old.run_id).deleted)
        self.assertEqual(registry.get_main(key).run_id, replacement.run_id)

    def test_incompatible_report_cannot_be_promoted_for_reference(self) -> None:
        module = _registry_module()
        registry = module.InMemoryReportRegistry()
        expected = _candidate("run-expected")
        incompatible = _candidate("run-other-scope", scope_hash="scope-b")
        key = reference_key_for_candidate(expected)
        registry.register_report(expected)
        registry.register_report(incompatible)

        with self.assertRaises(module.IncompatibleReference):
            registry.promote_main(
                key,
                incompatible.run_id,
                actor="analista",
                reason="tentativa inválida",
            )

    def test_auto_promotion_only_fills_empty_reference(self) -> None:
        module = _registry_module()
        registry = module.InMemoryReportRegistry()
        first = _candidate("run-a")
        second = _candidate("run-b")
        key = reference_key_for_candidate(first)
        registry.register_report(first)
        registry.register_report(second)

        self.assertTrue(registry.auto_promote_if_empty(key, first.run_id))
        self.assertFalse(registry.auto_promote_if_empty(key, second.run_id))
        self.assertEqual(registry.get_main(key).run_id, first.run_id)


if __name__ == "__main__":
    unittest.main()
