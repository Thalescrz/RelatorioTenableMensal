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


def first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lista metadados tecnicos recentes de exports VM, sem findings."
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    load_dotenv_file(args.env_file, override=True)
    credentials = CredentialConfig.from_environment()
    if not credentials.is_complete:
        parser.error("Credenciais Tenable VM nao configuradas.")
    jobs = _client_from_environment(credentials).list_export_jobs()
    safe_jobs: list[dict[str, Any]] = []
    for item in jobs[: max(1, args.limit)]:
        chunks = first_value(item, "chunks_available", "chunks")
        properties = item.get("properties")
        safe_jobs.append(
            {
                "export_uuid": first_value(item, "export_uuid", "uuid", "id"),
                "status": first_value(item, "status", "state"),
                "created_at": first_value(item, "created", "created_at", "createdAt"),
                "chunk_count": len(chunks) if isinstance(chunks, (list, dict)) else None,
                "property_count": len(properties) if isinstance(properties, list) else None,
            }
        )
    print(json.dumps(safe_jobs, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
