from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tenable_reports.config.environment import load_dotenv_file  # noqa: E402


SKIP_DIRS = {".git", ".venv", "__pycache__", "analysis_artifacts", "data", "outputs", "reports_generated"}
SKIP_FILES = {".env"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".yaml", ".yml", ".txt", ".example", ""}
SECRET_KEYS = {
    "TENABLE_ACCESS",
    "TENABLE_SECRET",
    "TCS_API_SECRET",
    "TENABLE_REPORTS_DB_PASSWORD",
    "TENABLE_REPORTS_ADMIN_PASSWORD",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Procura valores secretos do .env em arquivos publicaveis.")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    loaded = load_dotenv_file(ROOT / args.env_file, override=False)
    secrets = {
        value.encode("utf-8")
        for key, value in loaded.items()
        if value and key in SECRET_KEYS
    }
    if not secrets:
        print("secret_values=0 scanned_files=0 leaks=0")
        return 0

    scanned = 0
    leaks: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.name in SKIP_FILES or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        scanned += 1
        if any(secret in content for secret in secrets):
            leaks.append(str(path.relative_to(ROOT)))
    print(f"secret_values={len(secrets)} scanned_files={scanned} leaks={len(leaks)}")
    for path in leaks:
        print(path)
    return 1 if leaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
