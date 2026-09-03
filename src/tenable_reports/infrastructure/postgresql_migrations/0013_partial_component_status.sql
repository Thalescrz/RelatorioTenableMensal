alter table tenable_reports.web_batch_jobs
    drop constraint if exists web_batch_jobs_status_check;

alter table tenable_reports.web_batch_jobs
    add constraint web_batch_jobs_status_check check (
        status in (
            'QUEUED', 'RUNNING', 'WAITING_WAS_DECISION', 'COMPLETE',
            'COMPLETE_WITH_WARNINGS', 'PARTIALLY_COMPLETE', 'FAILED',
            'INTERRUPT_REQUESTED', 'INTERRUPTED', 'CANCELLED_BY_USER'
        )
    );
