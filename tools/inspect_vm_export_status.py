from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tenable_reports.cli import _client_from_environment  # noqa: E402
from tenable_reports.config.environment import CredentialConfig, load_dotenv_file  # noqa: E402


ALLOWED_SCALARS = {
    "status",
    "state",
    "total_chunks",
    "total_findings",
    "num_findings",
    "created",
    "created_at",
    "completed",
    "completed_at",
    "finished",
    "finished_at",
    "chunks_available_count",
    "chunks_cancelled",
    "chunks_failed",
    "empty_chunks_count",
    "finished_chunks",
    "reason",
}


def metadata_only(status: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"keys": sorted(status.keys())}
    for key in ALLOWED_SCALARS:
        value = status.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    for key in (
        "chunks_available",
        "chunks",
        "chunks_failed",
        "chunks_cancelled",
        "properties",
    ):
        value = status.get(key)
        if isinstance(value, (list, dict)):
            result[f"{key}_count"] = len(value)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Exibe somente metadados seguros de um status VM.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--export-uuid", required=True)
    args = parser.parse_args()
    load_dotenv_file(args.env_file, override=True)
    credentials = CredentialConfig.from_environment()
    if not credentials.is_complete:
        parser.error("Credenciais Tenable VM nao configuradas.")
    status = _client_from_environment(credentials).get_export_status(args.export_uuid)
    print(json.dumps(metadata_only(status), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
