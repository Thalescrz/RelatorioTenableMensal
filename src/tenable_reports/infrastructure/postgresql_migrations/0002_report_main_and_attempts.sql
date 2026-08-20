alter table tenable_reports.history_snapshots
    drop constraint if exists history_snapshots_competence_uq;

create unique index if not exists history_snapshots_run_uq
on tenable_reports.history_snapshots (run_id);

alter table tenable_reports.report_runs
    add column if not exists origin text not null default 'MANUAL';

alter table tenable_reports.report_runs
    add column if not exists logical_job_id text;

alter table tenable_reports.report_runs
    add column if not exists attempt_number integer not null default 1;

alter table tenable_reports.report_runs
    add column if not exists period_mode text;

alter table tenable_reports.report_runs
    add column if not exists timezone text;

alter table tenable_reports.report_runs
    add column if not exists scope_hash text;

alter table tenable_reports.report_runs
    add column if not exists metric_definition_version text;

alter table tenable_reports.report_runs
    add column if not exists deleted_at timestamptz;

alter table tenable_reports.report_runs
    add column if not exists deleted_by text;

alter table tenable_reports.report_runs
    add column if not exists deletion_reason text;

create index if not exists report_runs_logical_job_attempt_idx
on tenable_reports.report_runs (logical_job_id, attempt_number desc);

create index if not exists report_runs_client_reference_idx
on tenable_reports.report_runs (
    client_id, tenant_id, period_id, scope_hash, metric_definition_version,
    deleted_at, created_at desc
);

create table if not exists tenable_reports.report_main_references (
    reference_key text primary key,
    client_id text not null,
    tenant_id text not null,
    reference_kind text not null,
    period_key text not null,
    period_mode text not null,
    timezone text not null,
    scope_hash text not null,
    metric_definition_version text not null,
    run_id text not null unique references tenable_reports.report_runs(run_id)
        on delete restrict,
    set_by text not null,
    set_reason text not null,
    set_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists report_main_references_lookup_idx
on tenable_reports.report_main_references (
    client_id, tenant_id, reference_kind, period_key, scope_hash,
    metric_definition_version
);

create table if not exists tenable_reports.report_reference_events (
    reference_event_id bigint generated always as identity primary key,
    reference_key text not null,
    event_type text not null,
    previous_run_id text references tenable_reports.report_runs(run_id)
        on delete restrict,
    new_run_id text references tenable_reports.report_runs(run_id)
        on delete restrict,
    actor text not null,
    reason text not null,
    payload jsonb not null default '{}'::jsonb,
    event_at timestamptz not null default now()
);

create index if not exists report_reference_events_reference_idx
on tenable_reports.report_reference_events (reference_key, event_at desc);

create index if not exists report_reference_events_run_idx
on tenable_reports.report_reference_events (new_run_id, previous_run_id, event_at desc);
