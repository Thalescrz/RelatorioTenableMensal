create schema if not exists tenable_reports;
revoke all on schema tenable_reports from public;
revoke create on schema public from public;

create table if not exists tenable_reports.history_snapshots (
    snapshot_id text primary key,
    client_id text not null,
    tenant_id text not null,
    execution_type text not null,
    period_mode text not null,
    timezone text not null,
    metric_definition_version text not null,
    scope_hash text not null,
    period_id text not null,
    period_start_at timestamptz not null,
    period_end_at timestamptz not null,
    run_id text not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    constraint history_snapshots_competence_uq unique (
        client_id, tenant_id, execution_type, period_id, scope_hash,
        metric_definition_version
    )
);

create index if not exists history_snapshots_predecessor_idx
on tenable_reports.history_snapshots (
    client_id, tenant_id, execution_type, period_mode, timezone,
    metric_definition_version, scope_hash, period_end_at
);

create table if not exists tenable_reports.report_runs (
    run_id text primary key,
    client_id text not null,
    tenant_id text not null,
    execution_type text not null,
    period_id text,
    period_start_at timestamptz,
    period_end_at timestamptz,
    status text not null,
    storage_root text,
    dataset_path text,
    publication_manifest_path text,
    started_at timestamptz,
    ended_at timestamptz,
    error text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists report_runs_client_period_idx
on tenable_reports.report_runs (client_id, period_id, execution_type, created_at desc);

create index if not exists report_runs_status_idx
on tenable_reports.report_runs (status, created_at desc);

create table if not exists tenable_reports.publications (
    publication_id bigint generated always as identity primary key,
    run_id text not null unique references tenable_reports.report_runs(run_id)
        on delete restrict,
    status text not null,
    manifest_path text not null unique,
    manifest_sha256 text not null,
    source_dataset_path text not null,
    source_dataset_sha256 text not null,
    history_backend text,
    history_location text,
    distribution_performed boolean not null default false,
    payload jsonb not null,
    created_at timestamptz not null
);

create table if not exists tenable_reports.published_documents (
    document_id bigint generated always as identity primary key,
    publication_id bigint not null references tenable_reports.publications(publication_id)
        on delete cascade,
    path text not null,
    sha256 text not null,
    size_bytes bigint not null check (size_bytes >= 0),
    package_status text not null,
    created_at timestamptz not null default now(),
    constraint published_documents_path_uq unique (publication_id, path)
);

create index if not exists published_documents_publication_idx
on tenable_reports.published_documents (publication_id);

create table if not exists tenable_reports.orchestration_runs (
    run_id text primary key,
    orchestration_id text not null,
    mode text not null,
    status text not null,
    control_directory text not null,
    manifest_path text not null unique,
    notification_path text,
    client_count integer not null check (client_count >= 0),
    failed_count integer not null check (failed_count >= 0),
    payload jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists orchestration_runs_id_created_idx
on tenable_reports.orchestration_runs (orchestration_id, created_at desc);

create table if not exists tenable_reports.orchestration_clients (
    orchestration_client_id bigint generated always as identity primary key,
    orchestration_run_id text not null
        references tenable_reports.orchestration_runs(run_id) on delete cascade,
    client_id text not null,
    status text not null,
    exit_code integer,
    started_at timestamptz,
    ended_at timestamptz,
    duration_seconds numeric(16,3),
    publication_manifest_path text,
    log_path text,
    error text,
    payload jsonb,
    constraint orchestration_clients_run_client_uq
        unique (orchestration_run_id, client_id)
);

create index if not exists orchestration_clients_run_idx
on tenable_reports.orchestration_clients (orchestration_run_id);

create index if not exists orchestration_clients_client_status_idx
on tenable_reports.orchestration_clients (client_id, status, ended_at desc);

create table if not exists tenable_reports.events (
    event_id bigint generated always as identity primary key,
    event_key text not null unique,
    event_at timestamptz not null,
    event_type text not null,
    orchestration_run_id text references tenable_reports.orchestration_runs(run_id)
        on delete cascade,
    report_run_id text references tenable_reports.report_runs(run_id)
        on delete cascade,
    client_id text,
    payload jsonb not null default '{}'::jsonb
);

create index if not exists events_orchestration_idx
on tenable_reports.events (orchestration_run_id, event_at);

create index if not exists events_report_idx
on tenable_reports.events (report_run_id, event_at);

create table if not exists tenable_reports.artifacts (
    artifact_id bigint generated always as identity primary key,
    path text not null unique,
    kind text not null,
    sha256 text not null,
    size_bytes bigint not null check (size_bytes >= 0),
    client_id text,
    run_id text,
    source_root text,
    metadata jsonb not null default '{}'::jsonb,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now()
);

create index if not exists artifacts_client_run_idx
on tenable_reports.artifacts (client_id, run_id, kind);

create index if not exists artifacts_kind_seen_idx
on tenable_reports.artifacts (kind, last_seen_at desc);

create table if not exists tenable_reports.legacy_sqlite_imports (
    legacy_import_id bigint generated always as identity primary key,
    source_path text not null unique,
    source_sha256 text not null,
    source_kind text not null,
    record_count integer not null check (record_count >= 0),
    payload jsonb not null,
    imported_at timestamptz not null default now()
);
