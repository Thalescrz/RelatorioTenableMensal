from __future__ import annotations

import unittest

from tenable_reports.config.environment import CredentialConfig, EnvironmentError
from tenable_reports.config.profile import ClientProfile, ProfileError


BASE_PROFILE = {
    "schema_version": 1,
    "client_id": "client-001",
    "display_name": "Cliente",
    "tenant_id": "tenant",
}


class HistoricalCollectionProfileTests(unittest.TestCase):
    def test_legacy_profiles_keep_safe_historical_defaults(self) -> None:
        profile = ClientProfile.from_dict(BASE_PROFILE)

        self.assertEqual(profile.reporting.vm_export.historical_source, "legacy")
        self.assertEqual(
            profile.reporting.vm_export.historical_fallback,
            "warn_legacy",
        )
        self.assertEqual(profile.reporting.vm_export.manual_no_progress_seconds, 900)
        self.assertEqual(profile.reporting.vm_export.automatic_no_progress_seconds, 1800)

    def test_profile_accepts_inventory_beta_and_explicit_stall_limits(self) -> None:
        profile = ClientProfile.from_dict({
            **BASE_PROFILE,
            "reporting": {
                "vm_export": {
                    "historical_source": "inventory_beta",
                    "historical_fallback": "fail",
                    "manual_no_progress_seconds": 600,
                    "automatic_no_progress_seconds": 1200,
                }
            },
        })

        self.assertEqual(
            profile.reporting.vm_export.historical_source,
            "inventory_beta",
        )
        self.assertEqual(profile.reporting.vm_export.historical_fallback, "fail")
        self.assertEqual(profile.reporting.vm_export.manual_no_progress_seconds, 600)
        self.assertEqual(profile.reporting.vm_export.automatic_no_progress_seconds, 1200)

    def test_profile_rejects_unknown_historical_collection_values(self) -> None:
        cases = (
            ({"historical_source": "workbench"}, "historical_source"),
            ({"historical_fallback": "silent"}, "historical_fallback"),
            ({"manual_no_progress_seconds": 0}, "manual_no_progress_seconds"),
            ({"automatic_no_progress_seconds": 86401}, "automatic_no_progress_seconds"),
        )
        for vm_export, message in cases:
            with self.subTest(vm_export=vm_export):
                with self.assertRaisesRegex(ProfileError, message):
                    ClientProfile.from_dict({
                        **BASE_PROFILE,
                        "reporting": {"vm_export": vm_export},
                    })


class HistoricalCollectionEnvironmentTests(unittest.TestCase):
    def test_environment_uses_safe_no_progress_defaults(self) -> None:
        credentials = CredentialConfig.from_environment({
            "TENABLE_ACCESS": "access-fixture",
            "TENABLE_SECRET": "secret-fixture",
        })

        self.assertEqual(credentials.manual_no_progress_seconds, 900)
        self.assertEqual(credentials.automatic_no_progress_seconds, 1800)

    def test_environment_accepts_no_progress_overrides(self) -> None:
        credentials = CredentialConfig.from_environment({
            "TENABLE_ACCESS": "access-fixture",
            "TENABLE_SECRET": "secret-fixture",
            "TENABLE_EXPORT_MANUAL_NO_PROGRESS_SECONDS": "480",
            "TENABLE_EXPORT_AUTOMATIC_NO_PROGRESS_SECONDS": "1500",
        })

        self.assertEqual(credentials.manual_no_progress_seconds, 480)
        self.assertEqual(credentials.automatic_no_progress_seconds, 1500)

    def test_environment_rejects_invalid_no_progress_override(self) -> None:
        with self.assertRaisesRegex(
            EnvironmentError,
            "TENABLE_EXPORT_MANUAL_NO_PROGRESS_SECONDS",
        ):
            CredentialConfig.from_environment({
                "TENABLE_ACCESS": "access-fixture",
                "TENABLE_SECRET": "secret-fixture",
                "TENABLE_EXPORT_MANUAL_NO_PROGRESS_SECONDS": "zero",
            })


if __name__ == "__main__":
    unittest.main()
