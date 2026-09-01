alter table tenable_reports.web_batch_jobs
    add column if not exists phase text;

alter table tenable_reports.web_batch_jobs
    add column if not exists collection_checkpoint_path text;

alter table tenable_reports.web_batch_jobs
    add column if not exists remote_started_at timestamptz;

alter table tenable_reports.web_batch_jobs
    add column if not exists remote_ended_at timestamptz;

alter table tenable_reports.web_batch_jobs
    add column if not exists build_started_at timestamptz;

update tenable_reports.web_batch_jobs
set phase = 'LEGACY'
where phase is null;

alter table tenable_reports.web_batch_jobs
    alter column phase set default 'LEGACY';

alter table tenable_reports.web_batch_jobs
    alter column phase set not null;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'web_batch_jobs_phase_ck'
          and conrelid = 'tenable_reports.web_batch_jobs'::regclass
    ) then
        alter table tenable_reports.web_batch_jobs
            add constraint web_batch_jobs_phase_ck check (
                phase in (
                    'LEGACY', 'REMOTE_QUEUED', 'REMOTE_RUNNING',
                    'REMOTE_WAITING_DECISION', 'READY_FOR_BUILD',
                    'BUILD_RUNNING', 'TERMINAL'
                )
            );
    end if;
    if not exists (
        select 1
        from pg_constraint
        where conname = 'web_batch_jobs_checkpoint_path_ck'
          and conrelid = 'tenable_reports.web_batch_jobs'::regclass
    ) then
        alter table tenable_reports.web_batch_jobs
            add constraint web_batch_jobs_checkpoint_path_ck check (
                collection_checkpoint_path is null
                or (
                    (
                        collection_checkpoint_path ~ '^[A-Za-z]:[\\/]'
                        or collection_checkpoint_path ~ '^/'
                    )
                    and collection_checkpoint_path !~ '(^|[\\/])\.\.([\\/]|$)'
                )
            );
    end if;
end $$;

create index if not exists web_batch_jobs_phase_status_created_idx
on tenable_reports.web_batch_jobs (phase, status, created_at, position);

revoke all on table tenable_reports.web_batch_jobs from public;
