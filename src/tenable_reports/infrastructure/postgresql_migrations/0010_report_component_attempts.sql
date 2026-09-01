create table if not exists tenable_reports.report_component_attempts (
    id uuid primary key,
    client_id text not null check (btrim(client_id) <> ''),
    source_run_id text not null check (btrim(source_run_id) <> ''),
    component text not null check (
        component in ('VM_CORE', 'WAS', 'CLOUD')
    ),
    status text not null check (
        status in (
            'PENDING', 'RUNNING', 'COMPLETE', 'COMPLETE_WITH_WARNINGS',
            'FAILED', 'INTERRUPTED', 'SKIPPED'
        )
    ),
    stage text not null check (
        stage in (
            'COLLECTION', 'DATASET', 'RENDER', 'DOCUMENT_VALIDATION',
            'SNAPSHOT_PUBLICATION', 'REPORT_PUBLICATION'
        )
    ),
    attempt_number integer not null check (attempt_number >= 1),
    retryable boolean not null default false check (
        not retryable or status in ('FAILED', 'INTERRUPTED')
    ),
    failure_code text,
    failure_message text,
    checkpoint_path text check (
        checkpoint_path is null
        or (
            (checkpoint_path ~ '^[A-Za-z]:[\\/]' or checkpoint_path ~ '^/')
            and checkpoint_path !~ '(^|[\\/])\.\.([\\/]|$)'
        )
    ),
    artifact_references jsonb not null default '{}'::jsonb check (
        jsonb_typeof(artifact_references) = 'object'
    ),
    created_at timestamptz not null default now(),
    started_at timestamptz,
    ended_at timestamptz,
    constraint report_component_attempts_failure_code_required_ck check (
        status not in ('FAILED', 'INTERRUPTED') or failure_code is not null
    ),
    constraint report_component_attempts_failure_code_terminal_ck check (
        status in ('FAILED', 'INTERRUPTED') or failure_code is null
    ),
    constraint report_component_attempts_failure_message_terminal_ck check (
        status in ('FAILED', 'INTERRUPTED') or failure_message is null
    ),
    constraint report_component_attempts_failure_code_format_ck check (
        failure_code is null
        or failure_code ~ '^[A-Z][A-Z0-9_]{2,99}$'
    ),
    constraint report_component_attempts_failure_message_format_ck check (
        failure_message is null
        or (
            length(failure_message) <= 500
            and position(chr(10) in failure_message) = 0
            and position(chr(13) in failure_message) = 0
            and failure_message !~* '(access_key|secret_key|api_key|api_secret|api_token|cloud_token|token|password|authorization|bearer_token)[[:space:]]*[:=]'
        )
    ),
    constraint report_component_attempts_attempt_uq
        unique (source_run_id, component, attempt_number)
);

create index if not exists report_component_attempts_latest_idx
on tenable_reports.report_component_attempts (
    source_run_id, component, attempt_number desc
);

create index if not exists report_component_attempts_retryable_idx
on tenable_reports.report_component_attempts (
    source_run_id, component, attempt_number desc
)
where retryable and status in ('FAILED', 'INTERRUPTED');

revoke all on table tenable_reports.report_component_attempts from public;
