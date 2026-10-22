alter table tenable_reports.plugin_catalog
    add column if not exists cwes jsonb not null default '[]'::jsonb,
    add column if not exists owasp_categories jsonb not null default '[]'::jsonb;
