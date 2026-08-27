create table if not exists tenable_reports.cloud_report_snapshots (
    snapshot_id text primary key,
    schema_version integer not null,
    connector_version text not null,
    normalizer_version text not null,
    client_id text not null,
    tenant_id text not null,
    run_id text not null unique
        references tenable_reports.report_runs(run_id) on delete restrict,
    attempt_number integer not null check (attempt_number >= 1),
    execution_type text not null,
    period_mode text not null,
    timezone text not null,
    period_start_at timestamptz not null,
    period_end_at timestamptz not null,
    scope_hash text not null,
    metric_definition_version text not null,
    collected_at timestamptz not null,
    created_at timestamptz not null,
    content_sha256 text not null check (length(content_sha256) = 64),
    payload_gzip bytea not null,
    capabilities jsonb not null,
    record_counts jsonb not null,
    published_at timestamptz not null default now(),
    check (period_start_at < period_end_at)
);

create index if not exists cloud_report_snapshots_exact_idx
on tenable_reports.cloud_report_snapshots (
    client_id, tenant_id, execution_type, period_mode, timezone,
    scope_hash, metric_definition_version, connector_version,
    normalizer_version, schema_version, period_start_at, period_end_at,
    collected_at desc
);

create index if not exists cloud_report_snapshots_reuse_idx
on tenable_reports.cloud_report_snapshots (
    client_id, tenant_id, scope_hash, metric_definition_version,
    connector_version, normalizer_version, collected_at desc
);

create table if not exists tenable_reports.cloud_contract_checks (
    contract_check_id bigint generated always as identity primary key,
    client_id text not null,
    environment text not null,
    connector_version text not null,
    credential_revision text not null,
    endpoint text not null,
    required_ready boolean not null,
    capabilities jsonb not null,
    checked_at timestamptz not null,
    invalidated_at timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists cloud_contract_checks_lookup_idx
on tenable_reports.cloud_contract_checks (
    client_id, environment, connector_version, credential_revision,
    invalidated_at, checked_at desc
);

alter table tenable_reports.published_documents
    add column if not exists document_variant text;

alter table tenable_reports.published_documents
    drop constraint if exists published_documents_kind_check;

alter table tenable_reports.published_documents
    add constraint published_documents_kind_check
    check (
        document_kind is null
        or document_kind in ('base', 'custom', 'tag', 'cloud')
    );

alter table tenable_reports.published_documents
    drop constraint if exists published_documents_variant_check;

alter table tenable_reports.published_documents
    add constraint published_documents_variant_check
    check (
        (
            document_kind = 'cloud'
            and document_variant in ('base', 'expanded')
        )
        or (
            (document_kind is null or document_kind <> 'cloud')
            and document_variant is null
        )
    );

revoke all on table tenable_reports.cloud_report_snapshots from public;
revoke all on table tenable_reports.cloud_contract_checks from public;
