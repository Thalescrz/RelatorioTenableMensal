from __future__ import annotations

import unittest
from pathlib import Path

from tenable_reports import cli as cli_module
from tenable_reports.application.orchestration import (
    OrchestrationRequest,
    build_client_command,
    load_orchestration_config,
)


ROOT = Path(__file__).resolve().parents[1]


class HistoricalCliIntegrationTests(unittest.TestCase):
    def test_run_client_accepts_one_run_historical_source_override(self) -> None:
        args = cli_module.build_parser().parse_args([
            "run-client",
            "--profile",
            "profile.json",
            "--historical-source",
            "inventory-beta",
        ])
        self.assertEqual(args.historical_source, "inventory-beta")

    def test_orchestration_forwards_historical_source_override(self) -> None:
        config = load_orchestration_config(
            ROOT / "orchestration/clients.example.json"
        )
        client = config.clients[0]
        command = build_client_command(
            config=config,
            client=client,
            request=OrchestrationRequest(
                mode="manual",
                historical_source="inventory-beta",
            ),
            client_run_id="run-a",
        )
        index = command.index("--historical-source")
        self.assertEqual(command[index + 1], "inventory-beta")


if __name__ == "__main__":
    unittest.main()
