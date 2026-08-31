create table if not exists tenable_reports.web_batches (
    id uuid primary key,
    idempotency_key text not null,
    kind text not null check (
        kind in (
            'GENERATE_ONE', 'GENERATE_ALL', 'RETRY_INCOMPLETE',
            'RERUN_ALL', 'RECOVERED'
        )
    ),
    status text not null check (
        status in (
            'QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED',
            'STOP_REQUESTED', 'STOPPED', 'COMPLETE',
            'COMPLETE_WITH_FAILURES', 'COMPLETE_WITH_WARNINGS'
        )
    ),
    requested_action text check (
        requested_action is null
        or requested_action in ('PAUSE', 'RESUME', 'STOP')
    ),
    source_batch_id uuid references tenable_reports.web_batches(id),
    options jsonb not null default '{}'::jsonb,
    version bigint not null default 0 check (version >= 0),
    created_at timestamptz not null default now(),
    started_at timestamptz,
    ended_at timestamptz
);

create unique index if not exists web_batches_idempotency_uq
on tenable_reports.web_batches (idempotency_key);

create index if not exists web_batches_status_created_idx
on tenable_reports.web_batches (status, created_at desc);

create table if not exists tenable_reports.web_batch_jobs (
    id uuid primary key,
    batch_id uuid not null
        references tenable_reports.web_batches(id) on delete cascade,
    client_id text not null,
    position integer not null check (position >= 1),
    status text not null check (
        status in (
            'QUEUED', 'RUNNING', 'WAITING_WAS_DECISION', 'COMPLETE',
            'COMPLETE_WITH_WARNINGS', 'FAILED', 'INTERRUPT_REQUESTED',
            'INTERRUPTED', 'CANCELLED_BY_USER'
        )
    ),
    attempt_number integer not null default 1 check (attempt_number >= 1),
    payload jsonb not null default '{}'::jsonb,
    retry_of_batch_job_id uuid
        references tenable_reports.web_batch_jobs(id),
    worker_id text,
    process_id integer check (process_id is null or process_id > 0),
    control_file text,
    orchestration_run_id text
        references tenable_reports.orchestration_runs(run_id),
    logical_job_id text,
    run_id text,
    exit_code integer,
    error_code text,
    error_message text,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    ended_at timestamptz,
    unique (batch_id, client_id),
    unique (batch_id, position)
);

create unique index if not exists web_batch_jobs_active_client_uq
on tenable_reports.web_batch_jobs (client_id)
where status in (
    'QUEUED', 'RUNNING', 'WAITING_WAS_DECISION', 'INTERRUPT_REQUESTED'
);

create index if not exists web_batch_jobs_batch_position_idx
on tenable_reports.web_batch_jobs (batch_id, position);

create index if not exists web_batch_jobs_status_created_idx
on tenable_reports.web_batch_jobs (status, created_at);

create table if not exists tenable_reports.web_batch_events (
    id bigint generated always as identity primary key,
    batch_id uuid not null
        references tenable_reports.web_batches(id) on delete cascade,
    job_id uuid references tenable_reports.web_batch_jobs(id) on delete cascade,
    event_type text not null,
    actor text,
    idempotency_key text,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create unique index if not exists web_batch_events_idempotency_uq
on tenable_reports.web_batch_events (idempotency_key)
where idempotency_key is not null;

create index if not exists web_batch_events_batch_created_idx
on tenable_reports.web_batch_events (batch_id, created_at, id);

revoke all on table tenable_reports.web_batches from public;
revoke all on table tenable_reports.web_batch_jobs from public;
revoke all on table tenable_reports.web_batch_events from public;
