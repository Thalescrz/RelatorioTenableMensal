create table if not exists tenable_reports.compact_finding_snapshots (
    snapshot_id text primary key,
    schema_version integer not null,
    client_id text not null,
    tenant_id text not null,
    run_id text not null,
    execution_type text not null,
    period_mode text not null,
    period_start_at timestamptz not null,
    period_end_at timestamptz not null,
    content_sha256 text not null check (length(content_sha256) = 64),
    payload_gzip bytea not null,
    record_counts jsonb not null,
    document_references jsonb not null,
    created_at timestamptz not null,
    published_at timestamptz not null default now(),
    unique (client_id, tenant_id, run_id, period_start_at, period_end_at)
);

create index if not exists compact_finding_snapshots_exact_idx
    on tenable_reports.compact_finding_snapshots (
        client_id, tenant_id, period_start_at, period_end_at, published_at desc
    );

create index if not exists compact_finding_snapshots_run_idx
    on tenable_reports.compact_finding_snapshots (run_id);

revoke all on table tenable_reports.compact_finding_snapshots from public;
