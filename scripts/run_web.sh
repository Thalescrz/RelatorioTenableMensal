#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/.." && pwd -P)"
python_bin="$project_root/.venv/bin/python"
config_path="${1:-$project_root/orchestration/clients.json}"
port="${TENABLE_REPORTS_WEB_PORT:-8765}"

[[ -x "$python_bin" ]] || { printf 'PYTHON_ENVIRONMENT_MISSING\n' >&2; exit 1; }
exec "$python_bin" -m tenable_reports serve-web --project-root "$project_root" --config "$config_path" --host 127.0.0.1 --port "$port"
