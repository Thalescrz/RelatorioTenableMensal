create table if not exists tenable_reports.plugin_catalog (
    client_id text not null,
    tenant_id text not null,
    plugin_id bigint not null,
    name text,
    normalized_name text not null,
    family text,
    synopsis text,
    description text,
    solution text,
    reference_values jsonb not null default '[]'::jsonb,
    cves jsonb not null default '[]'::jsonb,
    cvss2_base_score double precision,
    cvss3_base_score double precision,
    vpr_score double precision,
    exploitable boolean,
    exploit_frameworks jsonb not null default '[]'::jsonb,
    provenance jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (client_id, tenant_id, plugin_id)
);

create index if not exists plugin_catalog_name_idx
    on tenable_reports.plugin_catalog (client_id, tenant_id, normalized_name);

revoke all on table tenable_reports.plugin_catalog from public;
