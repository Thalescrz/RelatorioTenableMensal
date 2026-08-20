from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tenable_reports.config.environment import CredentialConfig, load_dotenv_file
from tenable_reports.config.profile import ClientProfile, ProfileError, load_client_profile


ROOT = Path(__file__).resolve().parents[1]


class ProfileTests(unittest.TestCase):
    def test_example_profile_is_valid(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        self.assertEqual(profile.client_id, "cliente-exemplo")
        self.assertFalse(profile.presentation.vm_top5_include_output)
        self.assertFalse(profile.presentation.was_top5_include_output)
        self.assertFalse(profile.presentation.show_source_filters)
        self.assertEqual(profile.reporting.default_period, "previous_calendar_month")
        self.assertEqual(profile.reporting.manual_default_period, "rolling_calendar_month")
        self.assertFalse(profile.reporting.include_info_severity)
        self.assertEqual(profile.report.network_comparison_tags, ())
        self.assertEqual(
            profile.report.base_modules,
            ("summary", "infrastructure", "vm_top5", "was", "was_top5"),
        )
        self.assertFalse(profile.cloud_security_scope.enabled)

    def test_contrasting_profiles_are_declarative(self) -> None:
        standard = load_client_profile(
            ROOT / "clients/examples/client-profile-vm-standard.json"
        )
        expanded = load_client_profile(
            ROOT / "clients/examples/client-profile-intelligence-expanded.json"
        )
        self.assertEqual(standard.report.base_modules, expanded.report.base_modules)
        self.assertEqual(standard.report.intelligence_modules, ())
        self.assertGreater(len(expanded.report.intelligence_modules), 0)
        self.assertFalse(standard.was_scope.enabled)
        self.assertTrue(expanded.was_scope.enabled)
        self.assertFalse(standard.cloud_security_scope.enabled)
        self.assertTrue(expanded.cloud_security_scope.enabled)

    def test_profile_rejects_unknown_intelligence_module(self) -> None:
        with self.assertRaisesRegex(ProfileError, "modulo.*desconhecido"):
            ClientProfile.from_dict(
                {
                    "schema_version": 1,
                    "client_id": "client-001",
                    "display_name": "Cliente",
                    "tenant_id": "tenant",
                    "report": {"intelligence_modules": ["cliente_x_bloco"]},
                }
            )

    def test_profile_rejects_removing_a_base_module(self) -> None:
        with self.assertRaisesRegex(ProfileError, "nucleo padrao"):
            ClientProfile.from_dict(
                {
                    "schema_version": 1,
                    "client_id": "client-001",
                    "display_name": "Cliente",
                    "tenant_id": "tenant",
                    "report": {"base_modules": ["summary", "vm_top5"]},
                }
            )

    def test_capability_gates_cloud_and_was_modules(self) -> None:
        for module, message in (
            ("cloud_container_images", "cloud_security.enabled"),
            ("was_unsupported_tech", "was.enabled"),
        ):
            with self.subTest(module=module):
                with self.assertRaisesRegex(ProfileError, message):
                    ClientProfile.from_dict(
                        {
                            "schema_version": 1,
                            "client_id": "client-001",
                            "display_name": "Cliente",
                            "tenant_id": "tenant",
                            "report": {"intelligence_modules": [module]},
                        }
                    )

    def test_was_output_requires_was_capability(self) -> None:
        with self.assertRaisesRegex(ProfileError, "was_top5_include_output"):
            ClientProfile.from_dict(
                {
                    "schema_version": 1,
                    "client_id": "client-001",
                    "display_name": "Cliente",
                    "tenant_id": "tenant",
                    "presentation": {"was_top5_include_output": True},
                }
            )

    def test_profile_rejects_legacy_vm_tags_that_would_filter_general_data(self) -> None:
        with self.assertRaisesRegex(ProfileError, "network_comparison_tags"):
            ClientProfile.from_dict(
                {
                    "schema_version": 1,
                    "client_id": "client-001",
                    "display_name": "Cliente",
                    "tenant_id": "tenant",
                    "scope": {"vm": {"tags": ["Rede: Matriz"]}},
                }
            )

    def test_profile_rejects_embedded_secret(self) -> None:
        with self.assertRaisesRegex(ProfileError, "nao podem conter segredos"):
            ClientProfile.from_dict(
                {
                    "schema_version": 1,
                    "client_id": "client-001",
                    "display_name": "Cliente",
                    "tenant_id": "tenant",
                    "api_secret": "nao-pode",
                }
            )

    def test_dotenv_empty_values_override_stale_process_credentials(self) -> None:
        original_access = os.environ.get("TENABLE_ACCESS")
        original_secret = os.environ.get("TENABLE_SECRET")
        try:
            os.environ["TENABLE_ACCESS"] = "stale-access"
            os.environ["TENABLE_SECRET"] = "stale-secret"
            with tempfile.TemporaryDirectory() as directory:
                env_path = Path(directory) / ".env"
                env_path.write_text("TENABLE_ACCESS=\nTENABLE_SECRET=\n", encoding="utf-8")
                load_dotenv_file(env_path, override=True)
                credentials = CredentialConfig.from_environment()
                self.assertFalse(credentials.is_complete)
        finally:
            if original_access is None:
                os.environ.pop("TENABLE_ACCESS", None)
            else:
                os.environ["TENABLE_ACCESS"] = original_access
            if original_secret is None:
                os.environ.pop("TENABLE_SECRET", None)
            else:
                os.environ["TENABLE_SECRET"] = original_secret


if __name__ == "__main__":
    unittest.main()
