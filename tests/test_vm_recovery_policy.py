from __future__ import annotations

import unittest

from tenable_reports.application.vm_export_policy import recovery_vm_strategy
from tenable_reports.infrastructure.tenable_vm.client import ExportTimeoutError


class VmRecoveryPolicyTests(unittest.TestCase):
    def test_explicit_no_progress_retry_changes_combined_to_split(self) -> None:
        failure = ExportTimeoutError(
            "Export VM ficou sem progresso.",
            export_uuid="export-a",
            timeout_phase="no_progress",
            origin="created",
        )

        self.assertEqual(
            recovery_vm_strategy(
                current_strategy="combined",
                failure=failure,
                explicit_retry=True,
            ),
            "split",
        )

    def test_automatic_or_non_stall_retry_preserves_strategy(self) -> None:
        stalled = ExportTimeoutError(
            "Export VM ficou sem progresso.",
            timeout_phase="no_progress",
        )
        total = ExportTimeoutError(
            "Tempo total excedido.",
            timeout_phase="processing",
        )

        self.assertEqual(
            recovery_vm_strategy(
                current_strategy="combined",
                failure=stalled,
                explicit_retry=False,
            ),
            "combined",
        )
        self.assertEqual(
            recovery_vm_strategy(
                current_strategy="combined",
                failure=total,
                explicit_retry=True,
            ),
            "combined",
        )


if __name__ == "__main__":
    unittest.main()
