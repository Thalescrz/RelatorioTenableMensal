alter table tenable_reports.published_documents
    add column if not exists document_kind text;
alter table tenable_reports.published_documents
    add column if not exists tag_uuid text;
alter table tenable_reports.published_documents
    add column if not exists tag_category text;
alter table tenable_reports.published_documents
    add column if not exists tag_value text;

alter table tenable_reports.published_documents
    drop constraint if exists published_documents_kind_check;
alter table tenable_reports.published_documents
    add constraint published_documents_kind_check
    check (document_kind is null or document_kind in ('base', 'custom', 'tag'));

create index if not exists published_documents_tag_idx
on tenable_reports.published_documents (tag_uuid)
where tag_uuid is not null;
