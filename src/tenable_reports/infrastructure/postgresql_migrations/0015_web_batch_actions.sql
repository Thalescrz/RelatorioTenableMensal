alter table tenable_reports.web_batches
    drop constraint if exists web_batches_requested_action_check;

alter table tenable_reports.web_batches
    add constraint web_batches_requested_action_check check (
        requested_action is null
        or requested_action in (
            'PAUSE', 'RESUME', 'STOP', 'RETRY_INCOMPLETE', 'RERUN_ALL'
        )
    );
