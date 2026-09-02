alter table tenable_reports.web_batch_jobs
    add column if not exists vm_export_uuid uuid;

alter table tenable_reports.web_batch_jobs
    add column if not exists vm_resume_manifest_path text;

alter table tenable_reports.web_batch_jobs
    add column if not exists remote_export_started_at timestamptz;

alter table tenable_reports.web_batch_jobs
    add column if not exists remote_status_at timestamptz;

alter table tenable_reports.web_batch_jobs
    add column if not exists remote_progress_at timestamptz;
