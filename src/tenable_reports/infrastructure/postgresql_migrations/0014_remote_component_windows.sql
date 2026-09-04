alter table tenable_reports.web_batches
    add column if not exists root_batch_id uuid;

alter table tenable_reports.web_batches
    add column if not exists parent_batch_id uuid;

alter table tenable_reports.web_batches
    add column if not exists origin text;

alter table tenable_reports.web_batches
    add column if not exists competence text;

update tenable_reports.web_batches
set parent_batch_id = source_batch_id
where parent_batch_id is null
  and source_batch_id is not null;

with recursive batch_roots as (
    select id, id as root_id
    from tenable_reports.web_batches
    where source_batch_id is null

    union all

    select child.id, batch_roots.root_id
    from tenable_reports.web_batches child
    join batch_roots on child.source_batch_id = batch_roots.id
)
update tenable_reports.web_batches batches
set root_batch_id = batch_roots.root_id
from batch_roots
where batches.id = batch_roots.id
  and batches.root_batch_id is null;

update tenable_reports.web_batches
set root_batch_id = id
where root_batch_id is null;

update tenable_reports.web_batches
set origin = 'LEGACY'
where origin is null or btrim(origin) = '';

alter table tenable_reports.web_batches
    alter column root_batch_id set not null;

alter table tenable_reports.web_batches
    alter column origin set not null;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'web_batches_root_batch_fk'
          and conrelid = 'tenable_reports.web_batches'::regclass
    ) then
        alter table tenable_reports.web_batches
            add constraint web_batches_root_batch_fk
            foreign key (root_batch_id)
            references tenable_reports.web_batches(id);
    end if;
end $$;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'web_batches_parent_batch_fk'
          and conrelid = 'tenable_reports.web_batches'::regclass
    ) then
        alter table tenable_reports.web_batches
            add constraint web_batches_parent_batch_fk
            foreign key (parent_batch_id)
            references tenable_reports.web_batches(id);
    end if;
end $$;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'web_batches_competence_ck'
          and conrelid = 'tenable_reports.web_batches'::regclass
    ) then
        alter table tenable_reports.web_batches
            add constraint web_batches_competence_ck check (
                competence is null
                or competence ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
            );
    end if;
end $$;

create index if not exists web_batches_root_created_idx
on tenable_reports.web_batches (root_batch_id, created_at, id);

create index if not exists web_batches_origin_competence_idx
on tenable_reports.web_batches (origin, competence, created_at desc);

create table if not exists tenable_reports.web_batch_remote_components (
    id uuid primary key,
    batch_job_id uuid not null
        references tenable_reports.web_batch_jobs(id) on delete cascade,
    component text not null check (component in ('VM_CORE', 'WAS', 'CLOUD')),
    state text not null check (
        state in (
            'PENDING', 'RUNNING_WINDOW_1', 'RUNNING_WINDOW_2',
            'RUNNING_WINDOW_3', 'COMPLETE', 'COMPLETE_WITH_WARNINGS',
            'NOT_APPLICABLE', 'WAITING_MANUAL_RETRY',
            'NON_RETRYABLE_FAILURE', 'INTERRUPTED'
        )
    ),
    window_number integer not null check (window_number between 1 and 3),
    attempt_number integer not null check (attempt_number >= 1),
    parent_component_id uuid
        references tenable_reports.web_batch_remote_components(id),
    origin text not null,
    deadline_at timestamptz not null,
    replacement_created_in_window_2 boolean not null default false,
    replacement_created_in_window_3 boolean not null default false,
    identifier_kind text check (
        identifier_kind is null or identifier_kind in ('UUID', 'CURSOR', 'DATASET')
    ),
    remote_identifier text,
    identifier_origin text,
    query_fingerprint text,
    checkpoint_path text,
    completed_units integer not null default 0 check (completed_units >= 0),
    total_units integer check (total_units is null or total_units >= 0),
    last_remote_status text,
    last_contact_at timestamptz,
    last_progress_at timestamptz,
    worker_id text,
    lease_expires_at timestamptz,
    failure_code text,
    failure_message text check (
        failure_message is null or length(failure_message) <= 500
    ),
    retryable boolean not null default false,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    ended_at timestamptz,
    unique (batch_job_id, component, attempt_number),
    check (total_units is null or completed_units <= total_units),
    check (
        (identifier_kind is null and remote_identifier is null)
        or (identifier_kind is not null and remote_identifier is not null)
    ),
    check (not replacement_created_in_window_2 or window_number >= 2),
    check (not replacement_created_in_window_3 or window_number = 3),
    check (
        state not in ('WAITING_MANUAL_RETRY', 'NON_RETRYABLE_FAILURE')
        or failure_code is not null
    )
);

create index if not exists web_batch_remote_components_claim_idx
on tenable_reports.web_batch_remote_components (
    state, lease_expires_at, created_at, id
)
where state in (
    'PENDING', 'RUNNING_WINDOW_1', 'RUNNING_WINDOW_2', 'RUNNING_WINDOW_3'
);

create index if not exists web_batch_remote_components_job_attempt_idx
on tenable_reports.web_batch_remote_components (
    batch_job_id, attempt_number, component
);

create index if not exists web_batch_remote_components_parent_idx
on tenable_reports.web_batch_remote_components (parent_component_id)
where parent_component_id is not null;

revoke all on table tenable_reports.web_batch_remote_components from public;
