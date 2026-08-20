alter table tenable_reports.history_snapshots
    add column if not exists fingerprint_version text;

alter table tenable_reports.history_snapshots
    add column if not exists open_fingerprints bytea;

alter table tenable_reports.history_snapshots
    add column if not exists fixed_fingerprints bytea;

alter table tenable_reports.history_snapshots
    add column if not exists resurfaced_fingerprints bytea;

alter table tenable_reports.report_runs
    add column if not exists cleanup_status text not null default 'NOT_REQUIRED';

alter table tenable_reports.report_runs
    add column if not exists cleanup_completed_at timestamptz;

alter table tenable_reports.report_runs
    add column if not exists cleanup_bytes bigint not null default 0;

alter table tenable_reports.report_runs
    drop constraint if exists report_runs_cleanup_status_check;

alter table tenable_reports.report_runs
    add constraint report_runs_cleanup_status_check check (
        cleanup_status in ('NOT_REQUIRED', 'PENDING', 'COMPLETE', 'PARTIAL', 'FAILED')
    );

alter table tenable_reports.report_runs
    drop constraint if exists report_runs_cleanup_bytes_check;

alter table tenable_reports.report_runs
    add constraint report_runs_cleanup_bytes_check check (cleanup_bytes >= 0);

create index if not exists report_runs_cleanup_pending_idx
on tenable_reports.report_runs (cleanup_status, updated_at desc)
where cleanup_status in ('PENDING', 'PARTIAL', 'FAILED');
