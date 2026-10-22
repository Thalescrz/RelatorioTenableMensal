#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/.." && pwd -P)"
python_bin="$project_root/.venv/bin/python"
credential_root="$project_root/credentials"
database_env="$credential_root/database.env"
application_user=tenable_reports_app
database_name=tenable_reports

if ((EUID != 0)); then
  printf 'DATABASE_BOOTSTRAP_REQUIRES_ELEVATION\n' >&2
  exit 1
fi
[[ -x "$python_bin" ]] || { printf 'PYTHON_ENVIRONMENT_MISSING\n' >&2; exit 1; }
install -d -m 0700 -- "$credential_root"
application_password="$(openssl rand -base64 36 | tr -d '\n')"

runuser -u postgres -- psql --set=ON_ERROR_STOP=1 --set=app_password="$application_password" <<'SQL'
SELECT 'CREATE ROLE tenable_reports_app LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenable_reports_app')
\gexec
SELECT format('ALTER ROLE tenable_reports_app PASSWORD %L', :'app_password')
\gexec
SELECT 'CREATE DATABASE tenable_reports OWNER tenable_reports_app'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'tenable_reports')
\gexec
SQL

umask 077
{
  printf 'TENABLE_REPORTS_DB_HOST=127.0.0.1\n'
  printf 'TENABLE_REPORTS_DB_PORT=5432\n'
  printf 'TENABLE_REPORTS_DB_NAME=%s\n' "$database_name"
  printf 'TENABLE_REPORTS_DB_USER=%s\n' "$application_user"
  printf 'TENABLE_REPORTS_DB_PASSWORD=%s\n' "$application_password"
  printf 'TENABLE_REPORTS_DB_SSLMODE=prefer\n'
  printf 'TENABLE_REPORTS_DB_CONNECT_TIMEOUT=10\n'
  printf 'TENABLE_REPORTS_DB_APPLICATION_NAME=tenable-reports\n'
} > "$database_env"
chmod 0600 -- "$database_env"
application_password=''

"$python_bin" -m tenable_reports database-migrate --database-env-file "$database_env"
"$python_bin" -m tenable_reports database-status --database-env-file "$database_env"
