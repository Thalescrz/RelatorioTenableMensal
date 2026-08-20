from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tenable_reports.cli import _client_from_environment  # noqa: E402
from tenable_reports.config.environment import CredentialConfig, load_dotenv_file  # noqa: E402


INTERESTING_MARKERS = (
    "asset",
    "plugin",
    "port",
    "finding",
    "exploit",
    "vpr",
    "output",
    "description",
    "solution",
    "state",
    "first_found",
    "last_found",
    "last_fixed",
    "scan",
)


def walk_schema(value: Any, paths: dict[str, set[str]], prefix: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths[path].add(type(child).__name__)
            walk_schema(child, paths, path)
    elif isinstance(value, list):
        for child in value[:3]:
            walk_schema(child, paths, prefix + "[]")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lista somente nomes e tipos de campos de um chunk VM existente."
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--export-uuid", required=True)
    parser.add_argument("--chunk-id", type=int, required=True)
    parser.add_argument("--sample-records", type=int, default=100)
    args = parser.parse_args()

    load_dotenv_file(args.env_file, override=True)
    credentials = CredentialConfig.from_environment()
    if not credentials.is_complete:
        parser.error("Credenciais Tenable VM nao configuradas.")
    client = _client_from_environment(credentials)
    records = client.download_chunk(args.export_uuid, args.chunk_id)
    paths: dict[str, set[str]] = defaultdict(set)
    for record in records[: max(1, args.sample_records)]:
        walk_schema(record, paths)
    for path in sorted(paths):
        if any(marker in path.lower() for marker in INTERESTING_MARKERS):
            print(f"{path}: {','.join(sorted(paths[path]))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
