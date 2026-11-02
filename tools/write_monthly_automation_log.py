from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tenable_reports.application.automation_execution_log import (  # noqa: E402
    append_execution_log,
    build_execution_log_payload,
)


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-path", type=Path, required=True)
    args = parser.parse_args()
    raw = json.load(sys.stdin)
    payload = build_execution_log_payload(
        started_at=_datetime(str(raw["started_at"])),
        finished_at=_datetime(str(raw["finished_at"])),
        exit_code=int(raw["exit_code"]),
        stdout="\n".join(str(item) for item in raw.get("stdout") or ()),
        stderr="\n".join(str(item) for item in raw.get("stderr") or ()),
    )
    append_execution_log(args.log_path, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
