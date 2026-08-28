create table if not exists tenable_reports.was_recoveries (
    run_id text primary key,
    client_id text not null,
    tenant_id text not null,
    status text not null check (
        status in (
            'WAITING_WAS_DECISION', 'RETRY_AVAILABLE', 'RETRYING_WAS',
            'CONTINUING_WITHOUT_WAS', 'COMPLETE', 'EXPIRED'
        )
    ),
    checkpoint_path text not null,
    checkpoint jsonb not null,
    failure_code text,
    failure_message text,
    retryable boolean not null default false,
    export_uuid text,
    export_origin text,
    remote_status text,
    completed_chunks integer not null default 0 check (completed_chunks >= 0),
    total_chunks integer not null default 0 check (total_chunks >= 0),
    progress_made boolean not null default false,
    safe_cancel_available boolean not null default false,
    decision text check (
        decision is null
        or decision in ('continue_without_was', 'retry_was')
    ),
    idempotency_key text,
    decided_at timestamptz,
    completed_at timestamptz,
    expired_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists was_recoveries_pending_idx
on tenable_reports.was_recoveries (client_id, updated_at desc)
where status in ('WAITING_WAS_DECISION', 'RETRY_AVAILABLE');

create unique index if not exists was_recoveries_idempotency_uq
on tenable_reports.was_recoveries (idempotency_key)
where idempotency_key is not null;

revoke all on table tenable_reports.was_recoveries from public;
