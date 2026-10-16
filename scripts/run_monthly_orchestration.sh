#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/.." && pwd -P)"
python_bin="$project_root/.venv/bin/python"
config_path="${1:-$project_root/orchestration/clients.json}"
wait_timeout_seconds="${TENABLE_REPORTS_MONTHLY_WAIT_TIMEOUT_SECONDS:-108300}"

[[ -x "$python_bin" ]] || { printf 'PYTHON_ENVIRONMENT_MISSING\n' >&2; exit 1; }
exec "$python_bin" -m tenable_reports run-monthly-batch --project-root "$project_root" --config "$config_path" --wait-timeout-seconds "$wait_timeout_seconds"
